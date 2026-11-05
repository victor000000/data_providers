#!/usr/bin/env python3
"""Bring every still-trading symbol up to the latest available bar.

Workspace download rule: never re-download what is already on disk. This script
obeys it by asking Yahoo for `period="1mo"` only, and by appending rather than
rewriting - the stored history is not re-requested, and a symbol whose stored
bars already reach the newest one is skipped without a request at all.

The one full re-fetch is the `overlap differs` case below, and it is the
consistency exception rather than a shortcut: a corporate action restates every
earlier bar, so appending new bars onto the old basis would leave one file
holding two adjustment regimes. See scripts/96_verify_prices.py for the
re-download-and-diff shape this workspace uses for verification proper.

For each symbol the last few stored bars are re-fetched and compared with what
is on disk:

  * overlap matches  -> only the genuinely new bars are appended
  * overlap differs   -> a dividend or split happened since the last download,
                         so the whole series is refetched (the adjustment factor
                         changes for every earlier bar)

Delisted symbols are left alone: their last bar IS their end date. A symbol on
a current index roster is refreshed even when the window rule would skip it,
because it must still be trading.

Updates data/adjusted and data/raw in place; rebuild the database afterwards
with scripts/60_build_database.py.
"""
import csv
import os
import sys
import time

import math
import duckdb
import pandas as pd
import yfinance as yf

from data_providers.warehouse import config as C
from data_providers.providers.yahoo_session import create_session

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)

BATCH = 40
SLEEP = 0.8
OVERLAP = 5            # bars compared to detect corporate actions
ACTIVE_WINDOW_DAYS = 10   # a symbol trading within this many days of the newest bar is still listed
RECENT_DAYS = 180      # how far back a current index member may have stopped and still be worth a fetch


def fmt_date(d):
    return f"{d.day:02d}{d.month:02d}{d.year:04d}"


def key(ddmmyyyy):
    """DDMMYYYY -> YYYYMMDD so dates sort correctly as strings."""
    return ddmmyyyy[4:8] + ddmmyyyy[2:4] + ddmmyyyy[0:2]


def read_tail(path, n):
    """Last n (date, o,h,l,c,v) rows of a stored CSV, oldest first.

    Seeks to the end rather than reading the file: these files average ~400 KB
    and we only ever want the last few bars.
    """
    if not os.path.exists(path):
        return []
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - 512))
        chunk = fh.read().decode("ascii", "replace")
    rows = []
    for line in chunk.splitlines():
        p = line.rstrip("\r\n").split(",")
        if len(p) == 6 and p[0].isdigit() and len(p[0]) == 8:
            rows.append(p)
    return rows[-n:]


def append_rows(path, new):
    with open(path, "a", encoding="ascii") as fh:
        for d, o, h, l, c, v in new:
            fh.write(f"{d},{o:.4f},{h:.4f},{l:.4f},{c:.4f},{int(v)}\n")


def row_of(idx, r):
    o, h, l, c = r["Open"], r["High"], r["Low"], r["Close"]
    if not all(math.isfinite(v) for v in (o,h,l,c)):
        return None
    if "Adj Close" in r and not (math.isfinite(r["Adj Close"]) and r["Adj Close"]>0):
        return None
    # a sub-penny bar would be written as 0.0000 and the database rejects that
    if min(o, h, l, c) < C.MIN_PRICE:
        return None
    v = 0 if pd.isna(r["Volume"]) else int(r["Volume"])
    return fmt_date(idx.date()), o, h, l, c, v


def frame(src, s, multi):
    try:                     # one symbol's bars, normalised — or None when there are none
        df = src[s] if multi else src
        df = df.dropna(subset=["Close"])
        return df[~df.index.duplicated(keep="last")].sort_index()
    except Exception:                                        # noqa: BLE001
        return None


def rewrite(path, df, factor):
    from data_providers.warehouse.price_csv import rewrite_validated
    rewrite_validated(path, df, factor, C.MIN_PRICE)


def main():
    args = sys.argv[1:]
    # `--lagging` refreshes only the current index members whose file stops
    # short of the market's newest bar. That is the genuinely missing data; the
    # full run additionally re-checks every still-trading symbol, which is the
    # right thing after a long gap but re-fetches months of bars that are
    # already on disk when the tree is already current.
    only_lagging = "--lagging" in args
    nums = [a for a in args if a.isdigit()]
    limit = int(nums[0]) if nums else 0
    have = sorted(f[:-4] for f in os.listdir(C.ADJUSTED) if f.endswith(".csv"))

    # Refresh near the newest stored session; retain historical delisted tails.
    lasts = {}
    for s in have:
        t = read_tail(os.path.join(C.ADJUSTED, s + ".csv"), 1)
        if t:
            d = t[0][0]
            lasts[s] = pd.Timestamp(f"{d[4:8]}-{d[2:4]}-{d[0:2]}")
    if not lasts:
        print("no readable price files")
        return
    market_last = max(lasts.values())
    cutoff = market_last - pd.Timedelta(days=ACTIVE_WINDOW_DAYS)
    stale = set(s for s, d in lasts.items() if d >= cutoff)

    # The window rule misses a listed symbol whose last download happened
    # weeks ago: it stops looking "still trading" while it is trading perfectly
    # well, and the hole would sit in the middle of the dataset. A symbol on a
    # CURRENT index roster is by definition still listed, so it must have
    # current bars whatever its file says - no liveness request needed to know
    # it. (A file that stops early is usually a delisting, not staleness: check
    # Yahoo before assuming the download missed something.)
    try:
        con = duckdb.connect(os.path.join(C.ROOT, "yahoo.duckdb"), read_only=True)
        current = {r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM index_membership "
            "WHERE status = 'Current'").fetchall()}
        con.close()
    except Exception as e:                                       # noqa: BLE001
        current = set()
        print(f"  [warn] current-roster rule unavailable ({str(e)[:60]})")
    # ... bounded to the recent past: a current-roster symbol whose file ends
    # inside the window below was trading when it was last fetched, so a live
    # fetch is the right repair. Beyond it the symbol is delisted whatever the
    # roster says, and asking would only waste requests.
    recent = cutoff - pd.Timedelta(days=RECENT_DAYS)
    lagging = sorted(s for s in current
                     if s in lasts and recent <= lasts[s] < cutoff)
    if only_lagging:
        stale = lagging
    else:
        stale = sorted(stale | set(lagging))
    if limit:
        stale = stale[:limit]
    print(f"symbols: {len(have)}  newest bar in dataset: {market_last.date()}  "
          f"still-trading to refresh: {len(stale)}"
          + (f"  ({len(lagging)} current index members with a stale tail: "
             f"{lagging[:8]})" if lagging else ""), flush=True)

    session = create_session()
    app, full, skip, err = 0, 0, 0, 0
    t0 = time.time()
    for i in range(0, len(stale), BATCH):
        chunk = stale[i:i + BATCH]
        try:
            raw = yf.download(chunk, period="1mo", auto_adjust=False, session=session,
                              progress=False, group_by="ticker",
                              threads=True, timeout=60, actions=True)
        except Exception as e:                                   # noqa: BLE001
            err += len(chunk)
            print(f"  batch error: {str(e)[:70]}", flush=True)
            continue

        for s in chunk:
            df = frame(raw, s, len(chunk) > 1)
            if df is None or df.empty:
                # EMPTY in a batch != delisted: ~200 names read "possibly delisted" 2026-10-10,
                # all current alone; the skip stranded 193 (--lagging is members-only).
                df = frame(yf.download([s], period="1mo", auto_adjust=False, session=session, threads=False, progress=False, timeout=60), s, True)
            if df is None or df.empty:
                skip += 1
                continue

            ap = os.path.join(C.ADJUSTED, s + ".csv")
            rp = os.path.join(C.RAW, s + ".csv")
            stored = read_tail(ap, OVERLAP)
            close = df["Close"]
            adj = df["Adj Close"] if "Adj Close" in df else close
            f = (adj / close).where(close.notna() & (close != 0), 1.0)

            # compare overlap. Both sides must be ADJUSTED closes: the stored
            # file is adjusted, while the freshly fetched frame is raw.
            fresh = [r for r in (row_of(i2, r2) for i2, r2 in df.iterrows()) if r]
            # A row with an explicit missing adjusted close is not publishable.
            # row_of excludes it; a full rewrite validates every factor before opening a CSV.
            def _fac(day):
                v = f.get(pd.Timestamp(f"{day[4:8]}-{day[2:4]}-{day[0:2]}"), 1.0)
                return 1.0 if pd.isna(v) else float(v)
            newadj = {r[0]: r[4] * _fac(r[0]) for r in fresh}
            same = all(
                st[0] in newadj and abs(float(st[4]) - newadj[st[0]]) < 1e-3
                for st in stored)
            # YYYYMMDD ordering keeps January after December; stored DDMMYYYY does not.
            if stored:
                last_stored = key(stored[-1][0])
                todo = [r for r in fresh if key(r[0]) > last_stored]
            else:
                todo = list(fresh)

            if not same:
                # Corporate actions require a full-range refetch: a monthly slice
                # would discard older prices.
                try:
                    fdf = yf.download([s], start=C.DATA_START.isoformat(),
                                      auto_adjust=False, progress=False, session=session,
                                      group_by="ticker", threads=False,
                                      timeout=90, actions=True)[s]
                    fdf = fdf.dropna(subset=["Close"])
                    fdf = fdf[~fdf.index.duplicated(keep="last")].sort_index()
                except Exception:                                # noqa: BLE001
                    err += 1
                    continue
                if fdf.empty:
                    err += 1
                    continue
                fc = fdf["Close"]
                fa = fdf["Adj Close"] if "Adj Close" in fdf else fc
                ff = (fa / fc).where(fc.notna() & (fc != 0), 1.0)
                try:
                    rewrite(ap, fdf, ff)
                    rewrite(rp, fdf, 1.0)
                except ValueError:
                    err += 1
                    continue
                full += 1
            elif todo:
                # scale each new bar by that day's own adjustment factor
                adj_rows = []
                for d, o, h, l, c, v in todo:
                    k = _fac(d)
                    adj_rows.append((d, o * k, h * k, l * k, c * k, v))
                append_rows(ap, adj_rows)
                append_rows(rp, todo)
                app += 1

        el = time.time() - t0
        print(f"  {min(i+BATCH,len(stale))}/{len(stale)}  appended={app} "
              f"refetched={full} skipped={skip} err={err}  {el/60:.1f} min",
              flush=True)
        time.sleep(SLEEP)

    print(f"\nDONE appended={app} refetched={full} skipped={skip} errors={err}")
    return int(bool(stale) and skip + err >= len(stale))


if __name__ == "__main__":
    raise SystemExit(main())
