import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

# ── --help guard ─────────────────────────────────────────────────────────────
# This script WRITES to a database and takes no arguments, so `script.py --help`
# used to execute it in full. Not hypothetical: a 2026-10-05 sweep ran every app
# with --help and rewrote the fills table (via seed_fills.py) and risk_profile.
# A read-only-looking flag must not be a write. Exits before any connection.
from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap below, and a
# docstring must be the first statement, so all 33 copies printed "(no docstring)".

"""load_ibkr_flex.py — IBKR Flex Query XML -> trades.duckdb.

Parses FlexQueryResponse XML (per-day FlexStatements) and stores:
  * <Order> elements (levelOfDetail=ORDER) -> `orders` (executed orders w/ order ids, types, times)
  * <Trade> elements (levelOfDetail=EXECUTION) -> `fills` (dedup vs existing; IBKR-fee truth)
  * EquitySummaryByReportDateInBase + CashReportCurrency -> `account_daily` (daily equity/cash)
Times: dateTime/orderTime are America/New_York -> stored as UTC.

Usage: uv run python load_ibkr_flex.py <flex.xml ...>
"""
import datetime as dt
import glob
import pathlib
import sys
import xml.etree.ElementTree as ET

import duckdb
try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:
    NY = None

ROOT = bootstrap.ROOT
DB = ROOT / "trades.duckdb"


def parse_ts(s):
    """'YYYYMMDD;HHMMSS' (ET) -> UTC naive datetime"""
    if not s or ";" not in s:
        return None
    t = dt.datetime.strptime(s.replace(";", " "), "%Y%m%d %H%M%S")
    return (t.replace(tzinfo=NY).astimezone(dt.timezone.utc).replace(tzinfo=None)
            if NY else t + dt.timedelta(hours=4))


def contract_key(el):
    und = el.get("underlyingSymbol") or el.get("symbol")
    return (und, el.get("expiry") or "", el.get("strike") or "", el.get("putCall") or "")


def main():
    files = sys.argv[1:] or glob.glob(str(ROOT / "var/raw/ibkr_flex/*.xml"))
    con = duckdb.connect(str(DB))
    for path in files:
        src = pathlib.Path(path).name
        con.execute("DELETE FROM orders WHERE source=?", [src])
        tree = ET.parse(path)
        root = tree.getroot()
        n_ord = n_new = n_upd = n_acct = n_dupe = 0
        seen_trades = set()
        for stmt in root.iter("FlexStatement"):
            to_date = stmt.get("toDate")
            # ---- orders ----
            for o in stmt.iter("Order"):
                ts = parse_ts(o.get("orderTime") or o.get("dateTime"))
                oc = {"O": "OPEN", "C": "CLOSE"}.get((o.get("openCloseIndicator") or "")[:1])
                # (orders are idempotent already: this loop deletes its own source's
                # rows first -- DELETE FROM orders WHERE source=<filename> -- so a
                # re-import replaces rather than appends. Only FILLS lacked that.)
                con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [o.get("ibOrderID"), o.get("accountId"), "IBKR",
                             o.get("underlyingSymbol") or o.get("symbol"), o.get("symbol"),
                             {"BUY": "BUY", "SELL": "SELL"}.get(o.get("buySell")), oc,
                             o.get("orderType"), None, None, None, None, None,
                             f"EXECUTED (qty {o.get('quantity')} @ {o.get('tradePrice')})",
                             ts, o.get("listingExchange"), "USD",
                             f"openCloseIndicator={o.get('openCloseIndicator')}", src])
                n_ord += 1
            # ---- trades -> fills ----
            for t in stmt.iter("Trade"):
                if t.get("assetCategory") in ("CASH", "FXCONV"):
                    continue
                key = (t.get("transactionID"), t.get("ibExecID"))
                if key in seen_trades:
                    continue
                seen_trades.add(key)
                qty = float(t.get("quantity") or 0)
                if qty == 0:
                    continue
                action = "SELL" if qty < 0 else "BUY"
                q = abs(qty)
                px = float(t.get("tradePrice") or 0) or None
                fee = abs(float(t.get("ibCommission") or 0))
                try:
                    rl = float(t.get("fifoPnlRealized") or 0)
                except ValueError:
                    rl = 0.0
                # This maps "" -> None, and "" is what IBKR actually sends: on the
                # 2026-10-05 report all 328 <Trade> elements carried openCloseIndicator
                # either empty or "-", and the `notes` codes were only P/L, never O/C.
                # So open_close lands NULL for every Flex row -- the vendor field is
                # absent, not unread. See the note in fetch_tws_ibkr.py for the other
                # three paths that were checked before concluding that.
                oc = {"O": "OPEN", "C": "CLOSE"}.get((t.get("openCloseIndicator") or "")[:1])
                ts = parse_ts(t.get("dateTime"))
                trade_date = t.get("tradeDate")
                trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else None
                und, exp, K, cp = contract_key(t)
                sec_type = {"STK": "STOCK", "OPT": "OPT", "FUT": "FUT"}.get(t.get("assetCategory"),
                                                                           t.get("assetCategory"))
                expiry = dt.date(int(exp[:4]), int(exp[4:6]), int(exp[6:8])) if exp else None
                strike = float(K) if K else None
                # executions are inserted verbatim (dedup only by transactionID/execID) — never merged,
                # never matched against each other (same-date/price executions are common and distinct).
                # fill_id is a sequence default and the INSERT omits it, so a re-run
                # used to append a second copy of every execution -- doubling the log
                # of record and inflating every P&L, win-rate and fees number built
                # on it. `notes` carries the IBKR execID, which is unique per
                # execution; skip if we already hold it.
                _notes = f"conid {t.get('conid')}; execID {t.get('ibExecID')}"
                if con.execute("SELECT 1 FROM fills WHERE source='ibkr_flex' AND notes=? LIMIT 1",
                               [_notes]).fetchone():
                    n_dupe += 1
                    continue
                con.execute("""INSERT INTO fills (ts_utc, ts_orig, account, broker, underlying, symbol,
                    security_type, expiry, strike, call_put, action, open_close, qty, price, fee,
                    realized_pnl, exchange, currency, source, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [ts, f"{trade_date} {t.get('dateTime','').split(';')[-1]}", t.get("accountId"), "IBKR",
                     und, t.get("symbol"), sec_type, expiry, strike, cp or None, action, oc, int(q),
                     px, fee, rl if rl else None, t.get("listingExchange"), "USD", "ibkr_flex",
                     _notes])
                n_new += 1
            # ---- daily equity + cash (first row with a total per report date) ----
            for eq in stmt.iter("EquitySummaryByReportDateInBase"):
                rep = eq.get("reportDate")
                rep = f"{rep[:4]}-{rep[4:6]}-{rep[6:8]}" if rep else None
                if not rep or not eq.get("total"):
                    continue
                con.execute("DELETE FROM account_daily WHERE account=? AND day=CAST(? AS DATE)",
                            [stmt.get("accountId"), rep])
                con.execute("""INSERT INTO account_daily
                    (day, account, currency, net_liq, cash, source, notes) VALUES (?,?,?,?,?,?,?)""",
                    [rep, stmt.get("accountId"), "USD", float(eq.get("total")),
                     float(eq.get("cash")) if eq.get("cash") else None,
                     "ibkr_flex_daily", f"{src} (Flex daily equity)"])
                n_acct += 1
                break
        print(f"{src}: {n_ord} orders | +{n_new} fills, {n_upd} updated, {n_dupe} already held | {n_acct} account days")
    print("totals -> fills:", con.execute("SELECT count(*) FROM fills").fetchone()[0],
          "| orders:", con.execute("SELECT count(*) FROM orders").fetchone()[0],
          "| account_daily:", con.execute("SELECT count(*) FROM account_daily").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
