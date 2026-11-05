import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""backfill_us_window.py — before/after window data for every US fill.

For each underlying with US fills it stores into trades.duckdb:
  1. underlying_minutes — yfinance 5-min bars (incl. pre/post) within +-30 min of each fill
  2. underlying_daily   — last ~10 EOD daily bars (7-session before/after context)
Idempotent per source ('yfinance_5m' / 'yfinance_daily').

Usage: uv run python backfill_us_window.py [--span-min 30]
"""
import argparse
import datetime as dt
import pathlib

import duckdb
import yfinance as yf

DB = bootstrap.ROOT / "trades.duckdb"
CN = "20101589"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--span-min", type=int, default=30)
    args = ap.parse_args()
    con = duckdb.connect(str(DB))

    rows = con.execute("""SELECT DISTINCT underlying FROM fills
        WHERE account != ? AND underlying IS NOT NULL""", [CN]).fetchall()
    tickers = sorted(r[0] for r in rows)
    print("US underlyings with fills:", tickers)

    for tk in tickers:
        fills = con.execute("""SELECT ts_utc FROM fills WHERE underlying=? AND account != ? AND ts_utc IS NOT NULL""",
                            [tk, CN]).fetchall()
        if not fills:
            continue
        t = yf.Ticker(tk)
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        cutoff = now - dt.timedelta(days=7)
        cutoff60 = now - dt.timedelta(days=59)
        need_1m = any(f[0] >= cutoff for f in fills)
        need_5m = any(cutoff60 <= f[0] < cutoff for f in fills)
        need_1h = any(f[0] < cutoff60 for f in fills)
        h1 = t.history(period="7d", interval="1m", prepost=True) if need_1m else None
        h5 = t.history(period="60d", interval="5m", prepost=True) if need_5m else None
        hh = t.history(period="2y", interval="1h", prepost=True) if need_1h else None
        con.execute("DELETE FROM underlying_minutes WHERE symbol=? AND source IN ('yfinance_5m','yfinance_1m','yfinance_1h')", [tk])
        n = 0
        for hist, src in ((h1, "yfinance_1m"), (h5, "yfinance_5m"), (hh, "yfinance_1h")):
            if hist is None or hist.empty:
                continue
            hist = hist.dropna(subset=["Close"])
            idx = hist.index.tz_convert("UTC")
            wanted = {}
            for f in fills:
                ts = f[0]
                if src == "yfinance_1m" and ts < cutoff:
                    continue
                if src == "yfinance_5m" and not (cutoff60 <= ts < cutoff):
                    continue
                if src == "yfinance_1h" and ts >= cutoff60:
                    continue
                for j, it in enumerate(idx):
                    if abs((it.to_pydatetime().replace(tzinfo=None) - ts).total_seconds()) <= args.span_min * 60:
                        wanted[it] = j
            for it, j in sorted(wanted.items()):
                r = hist.iloc[j]
                con.execute("INSERT INTO underlying_minutes VALUES (?,?,?,?,?,?,?,?, ?)",
                            [it.to_pydatetime().replace(tzinfo=None), tk, float(r["Open"]), float(r["High"]),
                             float(r["Low"]), float(r["Close"]), float(r["Volume"]), None, src])
                n += 1
        days = sorted({f[0].date() for f in fills})
        # daily EOD context
        d = t.history(period="1mo", interval="1d")
        d = d.dropna(subset=["Close"]) if d is not None else d
        if d is not None and not d.empty:
            con.execute("DELETE FROM underlying_daily WHERE symbol=? AND source='yfinance_daily'", [tk])
            for it, r in d.tail(12).iterrows():
                con.execute("INSERT INTO underlying_daily VALUES (?,?,?,?,?,?,NULL,?,NULL,'yfinance_daily')",
                            [it.date(), tk, float(r["Open"]), float(r["High"]), float(r["Low"]),
                             float(r["Close"]), float(r["Volume"])])
        print(f"  {tk}: {n} 5m bars in fill windows ({len(days)} fill day(s)); daily bars stored")


if __name__ == "__main__":
    main()
