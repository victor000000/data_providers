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

"""load_schwab_orders.py — Schwab "Order Book" export -> `orders` table.

The Order Book CSV carries the full order lifecycle: placed / replaced / canceled
("U R OUT" = replaced) / filled / executions. Timestamps are China local (UTC+8) —
verified against the user's journal (10:09 ET fills = 22:09 export clock).

Handles: plain options, “N/M BACKRATIO <und> 100 D MON YY K1/K2 CP” and
“N STRANGLE <und> 100 D MON YY K1/K2 CALL/PUT” combos, cancel-reference rows.

Usage: uv run python load_schwab_orders.py <OrderBook.csv ...>
"""
import csv
import datetime as dt
import glob
import io
import pathlib
import re
import sys

import duckdb

ROOT = bootstrap.ROOT
from data_providers.jobs.trades import source_dirs  # noqa: E402
DB = ROOT / "trades.duckdb"
ACCT = "40342049SCHW"

SIMPLE = re.compile(
    r"^(SELL|BUY|SOLD|BOT)\s+([+-]?\d+(?:\.\d+)?)\s+([A-Z.]+)\s+(\d+)\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{2})\s+"
    # `\[?` not `\[`: execution rows carry no trailing "[TO CLOSE]" bracket, and the
    # mandatory bracket silently rejected them ("SOLD -1 META 100 20 NOV 26 850 CALL @11.55").
    r"([\d.]+)\s+(CALL|PUT)\s+@(-?[\d.]+)\s*(LMT|MKT|STP)?\s*\[?([A-Z /]+)?\]?")
# Combos carry more shapes than the first version knew, and today traded all of them:
#   SELL -50 DIAGONAL EWZ 100 18 DEC 26/20 NOV 26 43/32 PUT @2.65   (TWO expiries, slash)
#   SELL -6  CALENDAR CRWD 100 18 DEC 26/20 NOV 26 220 PUT @4.50
#   SELL -6  VERTICAL CRWD 100 20 NOV 26 230/220 PUT @1.82          (one expiry, two strikes)
#   BUY +25  2/3 BACKRATIO EWZ 100 16 OCT 26 44/45 CALL @-.31       (ratio prefix, NEGATIVE price)
# The old pattern required `BACKRATIO|STRANGLE` and a single expiry, so every diagonal
# parsed to a side and nothing else -- 43 of today's descriptions landed contractless.
COMBO = re.compile(
    r"^(SELL|BUY|SOLD|BOT)\s+([+-]?\d+(?:\.\d+)?)\s+"
    r"(?:(\d+/\d+)\s+)?"
    r"(BACKRATIO|STRANGLE|DIAGONAL|CALENDAR|VERTICAL)\s+([A-Z.]+)\s+(\d+)\s+"
    r"(\d{1,2}\s+[A-Z]{3}\s+\d{2}(?:/\d{1,2}\s+[A-Z]{3}\s+\d{2})?)\s+"
    r"([\d.]+(?:/[\d.]+)*)\s+([A-Z/]+)\s+@(-?[\d.]+)\s*(LMT|MKT)?\s*\[?([A-Z /]*)\]?")
# Plain equity/ETF legs: `SELL -46 DBB @25.07 LMT [TO CLOSE]`, `SOLD -46 DBB @25.0234`.
# No multiplier, expiry or strike -- the option-only patterns cannot match them, so
# today's six ETF liquidations (SPXL DSI IXC EDV IAU DBB) were recorded as bare sides.
STOCK = re.compile(
    r"^(SELL|BUY|SOLD|BOT)\s+([+-]?\d+(?:\.\d+)?)\s+([A-Z.]+)\s+"
    r"@(-?[\d.]+)\s*(LMT|MKT|STP)?\s*\[?([A-Z /]*)\]?$")
ACTION = {"SELL": "SELL", "SOLD": "SELL", "BUY": "BUY", "BOT": "BUY"}
OPENCLOSE = {"TO OPEN": "OPEN", "TO CLOSE": "CLOSE"}


def parse_desc(desc):
    d = re.sub(r"^\(Replacing #\d+\)\s*", "", desc).strip()
    m = SIMPLE.match(d)
    if m:
        side, qty, und, mult, dayn, mon, yr, strike, cp, px, otype, oc = m.groups()
        return dict(action=ACTION[side], qty=abs(float(qty)), underlying=und,
                    expiry=dt.datetime.strptime(f"{dayn} {mon} {yr}", "%d %b %y").date(),
                    strike=strike, cp=cp[0], px=float(px), otype=otype or "LMT",
                    oc={"TO OPEN": "OPEN", "TO CLOSE": "CLOSE"}.get((oc or "").strip().rstrip("/")),
                    symbol=f"{und} {strike}{cp[0]}")
    m = COMBO.match(d)
    if m:
        side, qty, ratio, kind, und, mult, exps, strikes, cp, px, otype, oc = m.groups()
        # A diagonal/calendar spells BOTH expiries ("18 DEC 26/20 NOV 26"). The column is
        # a single DATE, so the front leg is what goes in it; the full pair stays in
        # `symbol`, and the note keeps the broker's own wording. The strikes are likewise
        # a pair -- they are NOT one strike, and reading them as one is the mistake that
        # produced the phantom CRWD 225/220 mismatch earlier today.
        front = exps.split("/")[0].strip()
        return dict(action=ACTION[side], qty=abs(float(qty)), underlying=und,
                    expiry=dt.datetime.strptime(front, "%d %b %y").date(),
                    strike=strikes, cp=cp.replace("/", ""), px=float(px), otype=otype or "LMT",
                    oc=OPENCLOSE.get((oc or "").split("/")[0].strip()),
                    symbol=f"{und} {kind} {strikes} {cp}" + (f" {ratio}" if ratio else ""))
    m = STOCK.match(d)
    if m:
        side, qty, und, px, otype, oc = m.groups()
        return dict(action=ACTION[side], qty=abs(float(qty)), underlying=und, expiry=None,
                    strike=None, cp=None, px=float(px), otype=otype or "LMT",
                    oc=OPENCLOSE.get((oc or "").strip()), symbol=und)
    if d.startswith("Cancel Order"):
        return dict(action=None, qty=None, underlying=None, expiry=None, strike=None,
                    cp=None, px=None, otype=None, oc=None, symbol=None)
    return dict(action=ACTION.get(d.split(" ")[0]), qty=None, underlying=None, expiry=None,
                strike=None, cp=None, px=None, otype=None, oc=None, symbol=None)


def main():
    files = sys.argv[1:] or source_dirs.find("*OrderBook*.csv")
    con = duckdb.connect(str(DB))
    for path in files:
        raw = pathlib.Path(path).read_bytes()
        text = raw.decode("utf-8-sig", "replace")
        rows = list(csv.reader(io.StringIO("\n".join(text.splitlines()[3:]))))
        con.execute("DELETE FROM orders WHERE source=?", [pathlib.Path(path).name])
        n = 0
        for f in rows:
            if len(f) < 6 or not f[2].strip() or f[2].strip() == "Time":
                continue
            oid = f[3].strip().strip('="').lstrip("-+")
            desc, status = f[4].strip(), f[5].strip()
            try:
                ts = dt.datetime.strptime(f[2].strip(), "%m/%d/%y %H:%M:%S") - dt.timedelta(hours=8)
            except ValueError:
                ts = None
            p = parse_desc(desc)
            con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [oid, ACCT, "Schwab", p["underlying"], p["symbol"], p["action"], p["oc"],
                         p["otype"], None, p["px"], p["qty"], None, None, status or "EXECUTION", ts,
                         None, "USD", desc, pathlib.Path(path).name])
            n += 1
        print(f"{pathlib.Path(path).name}: {n} order events loaded")
    print("orders total:", con.execute("SELECT count(*) FROM orders").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
