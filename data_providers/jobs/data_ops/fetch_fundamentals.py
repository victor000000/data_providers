import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""fetch_fundamentals.py — market cap / shares / sector, and the index levels (VIX family).

  --mode caps    per-symbol snapshot (market cap, shares outstanding, float, sector,
                 industry) for the universe -> market_history.fundamentals_snapshot.
                 Source: yfinance. One row per (day, symbol): re-running the same day
                 replaces that day's row, never accumulates duplicates.
  --mode index   index levels with no home in the warehouse (it holds ETFs and stocks,
                 not indices): ^VIX, ^VIX9D, ^VIX3M, ^VVIX, ^GSPC, ^NDX, ^RUT ->
                 market_history.underlying_daily, source 'yfinance_index'.

    uv run python apps/data_ops/fetch_fundamentals.py \
        --mode index
    uv run python apps/data_ops/fetch_fundamentals.py \
        --mode caps --tickers-file plans/data_universe_tier1.txt
"""
import argparse
import datetime as dt
import math
import pathlib
import sys

from data_providers.data import clock  # noqa: E402
from data_providers.data import db  # noqa: E402

ROOT = bootstrap.ROOT
TODAY = clock.today()
INDEXES = ["^VIX", "^VIX9D", "^VIX3M", "^VVIX", "^GSPC", "^NDX", "^RUT"]
CAP_FALLBACK_UNIVERSE = ["SPY", "QQQ", "IWM"]

def mode_index(con):
    import socket
    import yfinance as yf
    from data_providers.providers.yahoo_session import create_session
    session = create_session()
    socket.setdefaulttimeout(25)   # yfinance spins forever on a flaky connection; never let it
    stored = 0
    for sym in INDEXES:
        try:
            h = yf.Ticker(sym, session=session).history(period="25y", interval="1d")
            print(f"  {sym}: fetched {0 if h is None else len(h)} rows", flush=True)
            if h is None or h.empty:
                continue
            rows = [[idx.date(), sym, float(r["Open"]), float(r["High"]), float(r["Low"]),
                     float(r["Close"]), None, float(r["Volume"]), None, "yfinance_index"]
                    for idx, r in h.iterrows()]
            con.execute("DELETE FROM mh.underlying_daily WHERE symbol=? AND source='yfinance_index'",
                        [sym])
            if rows:   # DuckDB raises on an empty list, not a no-op
                con.executemany("INSERT INTO mh.underlying_daily VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            print(f"  {sym}: stored {len(rows)} rows {rows[0][0]} → {rows[-1][0]}", flush=True)
            stored += 1
        except Exception as e:
            print(f"  {sym}: {repr(e)[:70]}", flush=True)
    return stored

def mode_caps(con, tickers):
    import socket
    import yfinance as yf
    from data_providers.providers.yahoo_session import create_session
    session = create_session()
    socket.setdefaulttimeout(25)
    stored = 0
    # Do not mistake US equities such as MA/MO for namespaced CN products.
    tickers = [t.upper() for t in tickers if not t.upper().startswith("CN:")]
    for tk in tickers:
        try:
            fi = yf.Ticker(tk, session=session).fast_info
            # NB: FastInfo.get() looks up the raw camelCase keys and returns None for the
            # snake_case name; attribute access is the one that works.
            mc = getattr(fi, "market_cap", None)
            sh = getattr(fi, "shares", None)
            if mc is None or sh is None or not all(math.isfinite(float(v)) and float(v)>0 for v in (mc,sh)):
                print(f"  {tk}: no usable equity cap/share snapshot"); continue
            con.execute("INSERT OR REPLACE INTO mh.fundamentals_snapshot VALUES (?,?,?,?,?,?,?,?)",
                        [TODAY, tk, mc, sh, None, None, None, "yfinance_fast_info"])
            print(f"  {tk}: cap {mc:,.0f}" if mc else f"  {tk}: no cap")
            stored += 1
        except Exception as e:
            print(f"  {tk}: {repr(e)[:60]}")
    return stored

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["caps", "index"], required=True)
    ap.add_argument("--tickers-file", default=str(ROOT / "plans" / "data_universe_tier1.txt"))
    args = ap.parse_args()
    con = db.connect(log=None, staging="yfinance", snapshot=True)
    if args.mode == "index":
        stored = mode_index(con)
    else:
        try:
            from data_providers.shared import files
            tickers = files.safe_read_ticker_file(args.tickers_file)
        except (FileNotFoundError, ValueError, OSError) as e:
            print(f"Error: {e}")
            tickers = []
        tickers = tickers or CAP_FALLBACK_UNIVERSE
        stored = mode_caps(con, tickers)
    con.close()
    if not stored:   # total wash-out only (2026-10-10: 15/15 refused and it exited 0)
        print(f"!! {args.mode} stored NOTHING — treating as failure")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
