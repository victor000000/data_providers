#!/usr/bin/env python3
"""Re-read symbols from Yahoo and DIFF them against what is stored. Writes nothing.

Workspace download rule: never re-download what is already on disk - VERIFYING
IS THE EXCEPTION. This is that exception, and the shape it has to take is the
reason it exists: it re-reads symbols already held, compares them, and writes
only a report. Never a price file - an overwrite would destroy the very thing
being checked. `grep "download rule" scripts/` finds this policy on every script
that uses the network.

Everywhere else, a fetch step tests "do we already have this?" first - see
needed() in 30_download_missing.py and symbols() in 37_fetch_actions.py, both of
which consult the download log before spending a request.

**A value that differs is not automatically an error.** Yahoo restates the whole
history whenever a symbol splits or pays a dividend, so a symbol that had a
corporate action since our fetch will differ at EVERY date by the same factor.
That is why the comparison is on the RATIO stored/live, not on the prices: a
flat ratio is a restatement and the stored row is still the row that was
fetched; a ratio that moves is the stored series genuinely disagreeing with the
source, which is the thing worth knowing.

  python3 scripts/96_verify_prices.py            # a 40-symbol sample
  python3 scripts/96_verify_prices.py N          # N symbols
  python3 scripts/96_verify_prices.py --all      # every symbol (slow, re-reads all)
  python3 scripts/96_verify_prices.py AAPL MSFT  # just these

Report: data/quarantine/price_verification.csv
"""
import csv
import os
import random
import sys
import time

import pandas as pd
import yfinance as yf

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)

LOG = os.path.join(C.QUARANTINE, "price_verification.csv")
BATCH = 20
SLEEP = 0.8
FLAT = 0.005          # ratio movement under this is a restatement, not a drift


def stored(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="ascii"):
        s = line.strip()
        if not s:
            continue
        p = s.split(",")
        if len(p) == 6 and len(p[0]) == 8 and p[0].isdigit():
            d = f"{p[0][4:8]}-{p[0][2:4]}-{p[0][:2]}"
            out[d] = float(p[4])
    return out


def compare(sym, live_close, live_adj):
    """-> dict for the report. Nothing here writes anything."""
    raw = stored(os.path.join(C.RAW, sym + ".csv"))
    adj = stored(os.path.join(C.ADJUSTED, sym + ".csv"))
    if not live_close:
        return {"symbol": sym, "verdict": "source-empty",
                "detail": "Yahoo returned nothing; stored data is the only copy"}
    if not raw:
        return {"symbol": sym, "verdict": "not-stored", "detail": "no local file"}

    common = sorted(set(raw) & set(live_close))
    only_local = sorted(set(raw) - set(live_close))
    only_live = sorted(set(live_close) - set(raw))
    if len(common) < 2:
        return {"symbol": sym, "verdict": "no-overlap",
                "detail": f"{len(raw)} stored, {len(live_close)} live, "
                          f"{len(common)} shared dates"}

    ratios = [raw[d] / live_close[d] for d in common if live_close[d] > 0]
    lo, hi = min(ratios), max(ratios)
    spread = (hi - lo) / lo if lo else 0.0
    # A restatement moves EVERY bar by the same factor; a data revision moves
    # one. Reporting the raw min-max spread cannot tell them apart - ULTI's 223
    # bars all sit within 0.02% of parity and a single one is 0.48% out, which
    # reads as a 0.56% "drift". The share of bars that actually disagree is the
    # question worth asking.
    flat = sorted(ratios)[len(ratios) // 2]
    off = sum(1 for r in ratios if abs(r / flat - 1) > 0.001) / len(ratios)
    # dates the live source has that we do not are ordinary if they are NEWER
    # than our last bar - that is a session we have not fetched yet, not a gap
    our_last = max(raw)
    new_days = [d for d in only_live if d > our_last]
    gaps = [d for d in only_live if d <= our_last]
    gone = [d for d in only_local if d <= our_last]

    if off > 0.05:
        verdict, detail = "DIFFERS", (
            f"{off:.0%} of {len(common)} shared dates disagree with the source "
            f"by more than 0.1% - not a restatement")
    elif off:
        verdict, detail = "ok", (
            f"{len(common)} shared dates at parity; {off:.1%} of bars differ "
            f"slightly (single-bar revisions, not a restatement)")
    elif gone:
        verdict, detail = "dates-missing-live", (
            f"{len(gone)} stored dates the source no longer returns, "
            f"e.g. {gone[:3]}")
    else:
        verdict, detail = "ok", (
            f"{len(common)} shared dates, ratio flat at {lo:.6f}"
            + (f"; {len(new_days)} newer sessions not yet fetched" if new_days else "")
            + (f"; {len(gaps)} gaps" if gaps else ""))
    return {"symbol": sym, "verdict": verdict, "detail": detail,
            "stored_rows": len(raw), "live_rows": len(live_close),
            "shared": len(common), "ratio_low": f"{lo:.6f}",
            "ratio_high": f"{hi:.6f}", "spread": f"{spread:.6f}"}


def fetch(batch):
    out = {}
    try:
        df = yf.download(batch, start=C.DATA_START.isoformat(),
                         auto_adjust=False, progress=False,
                         group_by="ticker", threads=True, timeout=90,
                         actions=True)
    except Exception:                                            # noqa: BLE001
        return out
    if df is None or not len(df):
        return out
    for sym in batch:
        try:
            sub = df[sym] if isinstance(df.columns, pd.MultiIndex) else df
        except (KeyError, TypeError):
            continue
        if "Close" not in sub:
            continue
        close = sub["Close"].dropna()
        out[sym] = ({i.date().isoformat(): float(v) for i, v in close.items()},
                    sub["Adj Close"].dropna() if "Adj Close" in sub else None)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    named = [a for a in args if not a.isdigit()]
    n = int(next((a for a in args if a.isdigit()), 40))
    if "--all" in sys.argv:
        todo = sorted(f[:-4] for f in os.listdir(C.ADJUSTED) if f.endswith(".csv"))
    elif named:
        todo = named
    else:
        every = sorted(f[:-4] for f in os.listdir(C.ADJUSTED) if f.endswith(".csv"))
        todo = random.Random(11).sample(every, min(n, len(every)))
    print(f"verifying {len(todo)} symbols against Yahoo "
          f"(this re-reads them; it writes no price file)", flush=True)

    rows, t0 = [], time.time()
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        live = fetch(batch)
        for sym in batch:
            lc, la = live.get(sym, ({}, None))
            rows.append(compare(sym, lc, la))
        el = time.time() - t0
        print(f"  {min(i+BATCH,len(todo))}/{len(todo)}  {el/60:.1f} min", flush=True)
        time.sleep(SLEEP)

    cols = ["symbol", "verdict", "detail", "stored_rows", "live_rows",
            "shared", "ratio_low", "ratio_high", "spread"]
    with open(LOG, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print()
    for k in sorted(counts, key=lambda x: -counts[x]):
        print(f"  {counts[k]:5}  {k}")
    bad = [r for r in rows if r["verdict"] in ("DIFFERS", "dates-missing-live")]
    for r in bad[:10]:
        print(f"    {r['symbol']:8} {r['verdict']:20} {r['detail'][:80]}")
    print(f"\n-> {LOG}")


if __name__ == "__main__":
    main()
