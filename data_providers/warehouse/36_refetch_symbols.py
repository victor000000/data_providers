#!/usr/bin/env python3
"""Re-download the FULL history for specific symbols, overwriting what is on disk.

Workspace download rule: never re-download what is already on disk. THIS SCRIPT
IS THE NAMED EXCEPTION, and it is the only way to overwrite a price file. That
is why it takes symbols one at a time from the command line and never sweeps:
the argument list IS the justification for each overwrite. Do not give it a
list of symbols that merely might be wrong - a re-fetch here destroys the stored
series, so an overwrite has to be the answer to a defect someone has already
diagnosed. To compare a symbol against the source WITHOUT overwriting it, use
scripts/96_verify_prices.py.

Used to repair files damaged by the corporate-action truncation bug, but also
handy whenever a single symbol needs a clean rebuild.

  python3 36_refetch_symbols.py LRCX TQQQ AVB
  python3 36_refetch_symbols.py --file /tmp/damaged.txt

After running this, rebuild the database (60) and regenerate the CSVs (70) so
the whole tree is consistent again.
"""
import csv
import os
import sys
import time

import pandas as pd
import yfinance as yf

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)


def fmt_date(d):
    return f"{d.day:02d}{d.month:02d}{d.year:04d}"


def write_csv(path, df, factor):
    scalar = not hasattr(factor, "get")
    with open(path, "w", encoding="ascii", newline="") as fh:
        for idx, r in df.iterrows():
            o, h, l, c = r["Open"], r["High"], r["Low"], r["Close"]
            # ALL FOUR, not just open and close: a NaN high or low was written as the
            # literal text "nan" (`f"{h*k:.4f}"` renders it that way), and
            # 60_build_database reads these files with ignore_errors=true, so the row
            # then vanished from the database while remaining in the file -- the file
            # and the table disagreeing about which bars exist.
            if pd.isna(o) or pd.isna(h) or pd.isna(l) or pd.isna(c):
                continue
            v = 0 if pd.isna(r["Volume"]) else int(r["Volume"])
            k = float(factor) if scalar else float(factor.get(idx, 1.0))
            fh.write(f"{fmt_date(idx.date())},{o*k:.4f},{h*k:.4f},"
                     f"{l*k:.4f},{c*k:.4f},{v}\n")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] == "--file":
        syms = [s.strip() for s in open(args[1]) if s.strip()]
    else:
        syms = args

    ok, fail = 0, []
    for s in syms:
        try:
            df = yf.download([s], start=C.DATA_START.isoformat(),
                             auto_adjust=False, progress=False,
                             group_by="ticker", threads=False,
                             timeout=90, actions=True)[s]
            df = df.dropna(subset=["Close"])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        except Exception as e:                                   # noqa: BLE001
            fail.append((s, str(e)[:50]))
            continue
        if df.empty:
            fail.append((s, "no data"))
            continue
        # drop bars that would be stored as 0.0000; the database rejects them
        df = df[(df["Open"] >= C.MIN_PRICE) & (df["High"] >= C.MIN_PRICE)
                & (df["Low"] >= C.MIN_PRICE) & (df["Close"] >= C.MIN_PRICE)]
        if df.empty:
            fail.append((s, "no bar with a storable price"))
            continue
        close = df["Close"]
        adj = df["Adj Close"] if "Adj Close" in df else close
        f = (adj / close).where(close.notna() & (close != 0), 1.0)
        # Then filter on the values that will actually be WRITTEN -- both legs. This is
        # the rule 30_download_missing.py:140-148 states and every other writer follows,
        # and this one did not: the check above tests the RAW prices only, so a raw bar
        # just above MIN_PRICE whose adjustment factor is small (a long split/dividend
        # history drives k well below 1) writes an adjusted price that formats to 0.0000.
        # 60_build_database then drops that row from the adjusted table, so the raw row
        # survives with adj_close NULL -- prices_adjusted serving a NULL price, and
        # symbols.adj_bars < bars failing the checklist.
        keep = ((df["Open"] * f >= C.MIN_PRICE) & (df["High"] * f >= C.MIN_PRICE)
                & (df["Low"] * f >= C.MIN_PRICE) & (df["Close"] * f >= C.MIN_PRICE))
        df, f = df[keep], f[keep]
        if df.empty:
            fail.append((s, "no bar with a storable price on both legs"))
            continue
        write_csv(os.path.join(C.ADJUSTED, s + ".csv"), df, f)
        write_csv(os.path.join(C.RAW, s + ".csv"), df, 1.0)
        print(f"  {s:9} {len(df):6} rows  {df.index[0].date()}..{df.index[-1].date()}")
        ok += 1
        time.sleep(0.3)

    print(f"\nrefetched {ok}/{len(syms)}")
    for s, why in fail:
        print(f"  FAILED {s}: {why}")


if __name__ == "__main__":
    main()
