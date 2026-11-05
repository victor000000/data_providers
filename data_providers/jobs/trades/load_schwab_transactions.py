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

"""load_schwab_transactions.py — Schwab transaction-history CSV -> fills.

Columns: Date(MM/DD/YYYY), Action, Symbol, Description, Quantity, Price, Fees & Comm, Amount.
Trade actions imported (dedup vs existing fills; Schwab fee truth applied). Non-trade rows
(dividends/taxes/interest/sweeps/transfers) are counted and skipped. No time in export ->
ts_utc = date 10:00 ET placeholder (ts_orig marks it).

Usage: uv run python load_schwab_transactions.py <Individual_*.csv ...>
"""
import csv
import datetime as dt
import glob
import pathlib
import re
import sys

from zoneinfo import ZoneInfo

import duckdb

ROOT = bootstrap.ROOT
from data_providers.jobs.trades import source_dirs  # noqa: E402
DB = ROOT / "trades.duckdb"
ACCT = "40342049SCHW"
OPT_SYM = re.compile(r"^([A-Z.]+)\s+(\d{2})/(\d{2})/(\d{4})\s+([\d.]+)\s+([CP])$")
TRADES = {"Buy": ("BUY", None), "Sell": ("SELL", None),
          "Buy to Open": ("BUY", "OPEN"), "Buy to Close": ("BUY", "CLOSE"),
          "Sell to Open": ("SELL", "OPEN"), "Sell to Close": ("SELL", "CLOSE")}


def money(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def main():
    files = sys.argv[1:] or source_dirs.find("Individual_*Transactions*.csv")
    con = duckdb.connect(str(DB))
    for path in files:
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        src = pathlib.Path(path).name
        n_new = n_upd = n_skip = 0
        for r in rows:
            action = (r.get("Action") or "").strip()
            if action not in TRADES:
                n_skip += 1
                continue
            act, oc = TRADES[action]
            sym = (r.get("Symbol") or "").strip()
            qty = abs(int(float(r.get("Quantity") or 0)))
            px = money(r.get("Price")) or None
            fee = money(r.get("Fees & Comm"))
            d = dt.datetime.strptime(r["Date"].strip(), "%m/%d/%Y").date()
            m = OPT_SYM.match(sym)
            if m:
                und, mm, dd, yyyy, K, cp = m.groups()
                expiry = dt.date(int(yyyy), int(mm), int(dd))
                strike, cp, sec_type = float(K), cp, "OPT"
            else:
                und, expiry, strike, cp, sec_type = sym, None, None, None, "STOCK"
            # 10:00 ET is a MARK, not a time: Schwab's transaction export has no time
            # for these rows, so the date is all we have. Converted through ZoneInfo
            # rather than a flat +4h, which is an hour wrong from 2026-11-01.
            ts = (dt.datetime.combine(d, dt.time(10, 0))
                  .replace(tzinfo=ZoneInfo("America/New_York"))
                  .astimezone(dt.timezone.utc).replace(tzinfo=None))
            exist = None
            for fid, easym, eqty, epx, eact, eund, eexp, eK, ecp in con.execute("""
                    SELECT fill_id, symbol, qty, price, action, underlying, expiry, strike, call_put
                    FROM fills WHERE account=? AND CAST(ts_utc AS DATE)=CAST(? AS DATE)
                      AND qty=? AND symbol=?""",
                    [ACCT, str(d), qty, sym]).fetchall():
                # symbol is now in the SQL filter. Without it, two DIFFERENT tickers
                # with the same qty/action/price on the same day matched each other:
                # for stock rows both strikes are None so that check passed, and the
                # second fill "updated" the first instead of being inserted -- one of
                # the two trades was silently lost from the log of record.
                if eact == act and (epx is None or px is None or abs(epx - px) < 0.005) and \
                        ((eK is None and strike is None) or (eK is not None and strike is not None and abs(float(eK) - strike) < 0.01)):
                    exist = fid
                    break
            if exist:
                con.execute("UPDATE fills SET fee=?, source='schwab_transactions' WHERE fill_id=?", [fee, exist])
                n_upd += 1
            else:
                con.execute("""INSERT INTO fills (ts_utc, ts_orig, account, broker, underlying, symbol,
                    security_type, expiry, strike, call_put, action, open_close, qty, price, fee,
                    currency, source, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [ts, f"{d} (no time in export)", ACCT, "Schwab", und, sym, sec_type, expiry,
                     strike, cp, act, oc, qty, px, fee, "USD", "schwab_transactions",
                     (r.get("Description") or "")[:120]])
                n_new += 1
        print(f"{src}: +{n_new} fills, {n_upd} fee updates, {n_skip} non-trade rows skipped")
    print("fills total:", con.execute("SELECT count(*) FROM fills").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
