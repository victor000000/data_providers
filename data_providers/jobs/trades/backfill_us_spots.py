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

"""backfill_us_spots.py — fill-time underlying prices + IV for US fills via yfinance 5m bars.

For every journal fill with a timestamp, find the nearest prior 5-minute bar
(including pre/post market), store the spot into fills.underlying_px and
underlying_ticks, and recompute iv_at_fill + greeks from the traded price at
that fill-time spot. Run:
  uv run python backfill_us_spots.py
"""
import datetime as dt
import pathlib
import sys

import duckdb

from data_providers.compute.bs import implied_vol, bs_greeks, T_years

import pandas as pd
import yfinance as yf

DB = bootstrap.ROOT / "trades.duckdb"
from data_providers.shared.rates import R_US as R

def main():
    con = duckdb.connect(str(DB))
    rows = con.execute("""
    SELECT fill_id, underlying, expiry, strike, call_put, price, ts_utc
    FROM fills
    WHERE broker IN ('IBKR','Schwab') AND ts_utc IS NOT NULL
      AND strike IS NOT NULL AND expiry IS NOT NULL
    ORDER BY fill_id""").fetchall()

    syms = sorted({r[1] for r in rows})
    bars = {}
    for s in syms:
        try:
            h = yf.Ticker(s).history(period="5d", interval="5m", prepost=True)
            if len(h):
                bars[s] = h
                print(f"{s}: {len(h)} 5m bars")
        except Exception as e:
            print(f"{s}: history failed — {repr(e)[:120]}")

    updated = 0
    for fid, sym, expiry, K, cp, price, ts in rows:
        h = bars.get(sym)
        if h is None or not len(h):
            continue
        t_ny = pd.Timestamp(ts, tz="UTC").tz_convert("America/New_York")
        pos = h.index.get_indexer([t_ny], method="pad")[0]
        if pos < 0:
            continue
        spot = float(h.iloc[pos]["Close"])
        T = T_years(expiry)
        call = cp == "C"
        iv = implied_vol(price, spot, K, T, R, call)
        g = bs_greeks(spot, K, T, R, iv, call) if iv else (None, None, None, None)
        con.execute("""UPDATE fills SET underlying_px=?, iv_at_fill=?,
        delta_at_fill=?, gamma_at_fill=?, theta_at_fill=?, vega_at_fill=? WHERE fill_id=?""",
            [spot, iv, g[0], g[1], g[2], g[3], fid])
        con.execute("INSERT INTO underlying_ticks VALUES (?,?,?,?,?)",
                    [ts, sym, spot, "yfinance_5m_backfill", f"fill #{fid}"])
        updated += 1
        print(f"fill #{fid} {sym} {K}{cp}: spot@fill={spot} iv={None if iv is None else round(iv,4)} "
              f"delta={None if g[0] is None else round(g[0],3)}")

    con.close()
    print(f"\nbackfilled {updated}/{len(rows)} fills")


# Guard added by the data/application split: unguarded, importing this module ran it.
if __name__ == "__main__":
    main()
