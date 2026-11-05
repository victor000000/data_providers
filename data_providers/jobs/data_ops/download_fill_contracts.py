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

"""download_fill_contracts.py — daily bars for the exact contracts we traded.

The anchor grid is built from price ratios at expiry−45d, so it rarely contains the strikes
our own fills landed on — which is why the O1 audit could only check 18 of 608 fills. This
pulls the full daily history of every distinct contract in `fills`, so the trade log holds
its own instruments completely and O1 can validate nearly every fill against an independent
tape.

Source: Alpaca daily option bars. The OCC symbol is built from the parsed columns, never
taken from the broker's spelling (Schwab writes 'MU 07/31/2026 755.00 P').

    uv run python apps/data_ops/download_fill_contracts.py
"""
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.request

from data_providers.data import db  # noqa: E402
from data_providers.shared.net import note_headers, retry, status, throttle  # noqa: E402

ROOT = bootstrap.ROOT
from data_providers.credentials import AlpacaHeaders
HDR = AlpacaHeaders()


def occ_of(underlying, expiry, cp, strike):
    return f"{str(underlying).strip().upper()}{expiry.strftime('%y%m%d')}{cp}" \
           f"{int(round(float(strike) * 1000)):08d}"


def get(url):
    def _once():
        req = urllib.request.Request(url, headers=HDR)
        r = urllib.request.urlopen(req, timeout=30)
        # Alpaca's own rate-limit headers say when the shared window is nearly exhausted;
        # note_headers() then waits for the advertised reset instead of guessing.
        note_headers("alpaca", r.headers)
        return json.load(r)
    return retry(_once, what=url[:90])


def main():
    con = db.connect(log=None, trades_ro=True, staging="alpaca")
    rows = con.execute("""
        SELECT DISTINCT underlying, expiry, strike, call_put, CAST(ts_utc AS DATE) AS first_day
        FROM tr.fills
        WHERE call_put IS NOT NULL AND expiry IS NOT NULL AND strike IS NOT NULL
          AND broker IN ('IBKR','Schwab')
        ORDER BY first_day""").fetchall()
    have = {r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM mh.option_bars WHERE source='alpaca'").fetchall()}
    todo = []
    for u, e, k, cp, day in rows:
        occ = occ_of(u, e, cp, k)
        if occ not in have:
            todo.append((occ, u, e, k, cp, day))
    print(f"distinct traded contracts: {len(rows)} · already held: {len(rows) - len(todo)} · "
          f"to fetch: {len(todo)}", flush=True)

    n = 0
    for occ, u, e, k, cp, day in todo:
        try:
            r = get(f"https://data.alpaca.markets/v1beta1/options/bars?symbols={occ}"
                    f"&timeframe=1Day&start={max(day - dt.timedelta(days=7), dt.date(2024, 2, 1))}"
                    f"&limit=10000")
            bars = r.get("bars", {}).get(occ, [])
        except Exception as ex:
            print(f"  {occ}: {repr(ex)[:60]}", flush=True); time.sleep(1); continue
        if bars:
            con.executemany("""INSERT INTO mh.option_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT DO NOTHING""",
                            [["alpaca", occ, u, e, k, cp, b["t"][:10], b["o"], b["h"], b["l"],
                              b["c"], b["v"], b.get("vw")] for b in bars])
            n += len(bars)
            print(f"  {occ}: {len(bars)} bars", flush=True)
        else:
            print(f"  {occ}: no bars", flush=True)
        status("download_fill_contracts", contract=occ, bars_total=n)
        throttle('alpaca')
    print(f"fill-contract bars: +{n}")
    con.close()


if __name__ == "__main__":
    main()
