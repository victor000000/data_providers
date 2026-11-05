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

"""seed_cboe.py — load frozen CBOE 2026-09-29 03:44 ET chain snapshots (plan legs) into the DB.
CBOE files carry vendor IV + greeks + OI + volume, stored as-is.
Files at data/cboe_2026-09-29_0344/{SYM}_opt.json
"""
import datetime as dt
import json
import pathlib

import duckdb

ROOT = bootstrap.ROOT
DATA = ROOT / "var" / "raw" / "cboe" / "2026-09-29_0344"
DB = ROOT / "trades.duckdb"

LEGS = {
    "META": [(650, "P"), (630, "P"), (850, "C")],
    "CRWD": [(230, "P"), (220, "P"), (330, "C")],
    "ILMN": [(240, "P"), (230, "P"), (340, "C")],
    "SWKS": [(75, "P"), (115, "C")],
    "EWZ":  [(32, "P"), (43, "C")],
    "TLT":  [(74, "P"), (83, "C")],
}
EXPIRY = dt.date.fromisoformat("2026-11-20")
STAMP = "261120"

def main():
    con = duckdb.connect(str(DB))
    n = 0
    for sym, legs in LEGS.items():
        f = DATA / f"{sym}_opt.json"
        if not f.exists():
            print(f"{sym}: file missing, skipped")
            continue
        d = json.load(open(f))
        ts = dt.datetime.strptime(d["timestamp"], "%Y-%m-%d %H:%M:%S") + dt.timedelta(hours=4)  # ET->UTC
        spot = d["data"]["current_price"]
        snap_id = con.execute("""INSERT INTO chain_snapshots (ts_utc, underlying, expiry, spot, source)
        VALUES (?,?,?,?, 'cboe_frozen_0344') RETURNING snap_id""", [ts, sym, EXPIRY, spot]).fetchone()[0]
        for o in d["data"]["options"]:
            osym = o["option"]
            if STAMP not in osym or not osym.startswith(sym):
                continue
            strike = int(osym[-8:]) / 1000.0
            cp = "C" if osym[-9] == "C" else "P"
            if (strike, cp) not in legs:
                continue
            bid, ask = o.get("bid"), o.get("ask")
            mid = (bid + ask) / 2 if bid is not None and ask is not None else None
            con.execute("INSERT INTO chain_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [snap_id, sym, EXPIRY, strike, cp, bid, ask,
                         o.get("bid_size"), o.get("ask_size"), mid,
                         o.get("iv"), o.get("delta"), o.get("gamma"), o.get("theta"), o.get("vega"),
                         o.get("open_interest"), o.get("volume"), spot])
            n += 1
    con.close()
    print(f"seeded {n} plan-leg contracts from CBOE frozen snapshot")


# Guard added by the data/application split: unguarded, importing this module ran it.
if __name__ == "__main__":
    main()
