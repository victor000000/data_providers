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

"""load_ibkr_activity.py — IBKR Activity Statement CSV -> trades.duckdb.

Reads multi-section Activity CSVs (Trades / Open Positions / NAV / Change in NAV /
Account Information). For period statements:
  * Trades section -> fills (options + stocks); merges with existing journal fills
    (same date/qty/price/action + normalized contract) by updating fees/realized,
    inserts genuinely new fills otherwise.
  * NAV -> one account_daily row dated at the statement period end.

Usage: uv run python load_ibkr_activity.py <U*.csv ...>
"""
import csv
import datetime as dt
import glob
import pathlib
import re
import sys

import duckdb
try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:                      # pragma: no cover
    NY = None

ROOT = bootstrap.ROOT
from data_providers.jobs.trades import source_dirs  # noqa: E402
DB = ROOT / "trades.duckdb"
ACT = "REPLACE_WITH_IBKR_ACCOUNT"
MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
IBKR_SYM = re.compile(r"^([A-Z.]+)\s+(\d{1,2})([A-Z]{3})(\d{2})\s+([\d.]+)\s+([PC])$")
JOURNAL_SYM = re.compile(r"^([A-Z.]+)\s+([A-Za-z]{3})(\d{1,2})\s+([\d.]+)\s*([PC])$")


def norm_sym(sym):
    """-> (underlying, date, strike, cp) for option symbols in either house style, else None."""
    s = (sym or "").strip()
    m = IBKR_SYM.match(s)
    if m:
        und, d, mon, yr, K, cp = m.groups()
        return und, dt.date(2000 + int(yr), MON[mon], int(d)), float(K), cp
    m = JOURNAL_SYM.match(s)
    if m:
        und, mon, d, K, cp = m.groups()
        return und.upper(), dt.date(2026, MON[mon.upper()], int(d)), float(K), cp
    return None


def to_utc(date_s, time_s):
    t = dt.datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S")
    return (t.replace(tzinfo=NY).astimezone(dt.timezone.utc).replace(tzinfo=None) if NY else t + dt.timedelta(hours=4))


def main():
    files = sys.argv[1:] or source_dirs.find("U*_20*.csv")
    con = duckdb.connect(str(DB))
    for path in files:
        rows = list(csv.reader(open(path, encoding="utf-8-sig")))
        section = None
        header = None
        trades, nav, period_end = [], {}, None
        for r in rows:
            if len(r) < 2:
                continue
            sec, kind = r[0], r[1]
            if sec == "Statement" and kind == "Data" and r[2] == "Period":
                m = re.search(r"-\s*([A-Z][a-z]+ \d{1,2}, \d{4})", r[3])
                if m:
                    period_end = dt.datetime.strptime(m.group(1), "%B %d, %Y").date()
            if kind == "Header":
                section, header = sec, r[2:]
                continue
            if kind != "Data" or section is None:
                continue
            d = dict(zip(header, r[2:]))
            if sec == "Trades" and d.get("DataDiscriminator") in ("Order", "Trade"):
                trades.append(d)
            elif sec == "Net Asset Value" and str(d.get("Asset Class", "")).strip() == "Total":
                nav["total"] = float(d["Current Total"])
            elif sec == "Net Asset Value" and d.get("Asset Class", "").strip() == "Cash":
                nav["cash"] = float(d["Current Total"])
        if not trades and not nav:
            print(f"{pathlib.Path(path).name}: nothing usable"); continue

        n_new = n_upd = 0
        for d in trades:
            if d.get("Asset Category") == "Forex":
                continue
            sym_raw = d["Symbol"].strip()
            qty = float(d["Quantity"] or 0)
            if qty == 0:
                continue
            action = "SELL" if qty < 0 else "BUY"
            q = abs(qty)
            px = float(d["T. Price"]) if d["T. Price"] not in ("--", "") else None
            fee = abs(float(d["Comm/Fee"] or 0))
            try:
                rl = float(d["Realized P/L"] or 0)
            except ValueError:
                rl = 0.0
            code = (d.get("Code") or "").split(";")[0]
            oc = {"O": "OPEN", "C": "CLOSE"}.get(code)
            ds, ts_ = d["Date/Time"].split(", ")
            ts = to_utc(ds, ts_)
            opt = norm_sym(sym_raw)
            und = opt[0] if opt else sym_raw
            expiry = opt[1] if opt else None
            strike = opt[2] if opt else None
            cp = opt[3] if opt else None
            sec_type = "OPT" if opt else "STOCK"
            # look for an existing fill of the same contract on the same day with same qty/price/action
            exist = None
            if px is not None:
                for fid, esym, eqty, epx, eact, efee, erl in con.execute("""
                        SELECT fill_id, symbol, qty, price, action, fee, realized_pnl FROM fills
                        WHERE account=? AND CAST(ts_utc AS DATE)=CAST(? AS DATE) AND qty=? AND price=?""",
                        [ACT, ds, int(q), px]).fetchall():
                    if eact == action and norm_sym(esym) == opt:
                        exist = (fid, efee, erl); break
            if exist:
                fid, efee, erl = exist
                con.execute("UPDATE fills SET fee=?, realized_pnl=COALESCE(NULLIF(realized_pnl,0), NULLIF(?,0)), source='ibkr_activity' WHERE fill_id=?",
                            [fee, rl, fid])
                n_upd += 1
            else:
                con.execute("""INSERT INTO fills (ts_utc, ts_orig, account, broker, underlying, symbol,
                    security_type, expiry, strike, call_put, action, open_close, qty, price, fee,
                    realized_pnl, exchange, currency, source, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [ts, f"{ds} {ts_}", ACT, "IBKR", und,
                     sym_raw, sec_type, expiry, strike, cp, action, oc, int(q),
                     px, fee, rl if rl else None, None, "USD", "ibkr_activity",
                     f"from Activity Statement {ds}; code {d.get('Code','')}"])
                n_new += 1
        note = (f"IBKR Activity period statement end {period_end}; NAV total {nav.get('total')} "
                f"cash {nav.get('cash')}; imported {n_new} new fills, updated {n_upd}")
        if nav.get("total"):
            con.execute("DELETE FROM account_daily WHERE account=? AND day=CAST(? AS DATE)", [ACT, str(period_end)])
            con.execute("""INSERT INTO account_daily
                (day, account, currency, net_liq, cash, source, notes) VALUES (?,?,?,?,?,?,?)""",
                [str(period_end), ACT, "USD", nav["total"], nav.get("cash"), "ibkr_activity_period", note])
        print(f"{pathlib.Path(path).name}: +{n_new} fills, {n_upd} fee updates | {note}")
    print("fills total:", con.execute("SELECT count(*) FROM fills").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
