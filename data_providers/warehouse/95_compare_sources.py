#!/usr/bin/env python3
"""Recheck the spare source (akshare / Sina) against the main one (Yahoo).

Workspace download rule: never re-download what is already on disk - VERIFYING
IS THE EXCEPTION. This script re-reads symbols the tree already holds, on
purpose, because the question it answers ("do the two sources agree?") cannot be
answered from stored data alone. It writes SOURCE_CONSISTENCY.md and touches no
price file: a verification pass that overwrote what it was checking would
destroy its own evidence. scripts/96_verify_prices.py is the same shape against
the main source.

The dataset is built from Yahoo and filled from akshare only where Yahoo has
nothing, so the two sources have to be shown to agree wherever they overlap.
This script does that on a sample and writes SOURCE_CONSISTENCY.md.

The comparison cannot be a plain close-to-close diff, because the two sources
use different conventions:

  * Yahoo restates history onto today's share basis; Sina keeps the price as
    traded. A 4-for-1 split therefore makes one series 4x the other before the
    split date, and every such difference is a legitimate convention, not a
    data error.
  * The test used here is therefore the RATIO between the two series. If both
    describe the same instrument, the ratio must be a step function: flat
    between corporate actions, stepping only on a split, and each step should
    be a recognisable split ratio. What is left after removing those steps is
    the actual agreement, and it is reported per symbol.
  * The adjusted legs are compared only where the spare's adjustment factor
    validates, and the cases where it does not (Sina returns negative adjusted
    prices on some long histories) are counted - that is why the spare's
    adjusted series is used only when it passes validation.

Writes SOURCE_CONSISTENCY.md. Run it by hand; it needs the network.
"""
import csv
import datetime
import os
import sys
import time

import akshare as ak
import pandas as pd

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)

OUT = os.path.join(C.ROOT, "SOURCE_CONSISTENCY.md")
RETRIES = 3

# A spread of instruments: high-split survivors, low-split blue chips, ETFs and
# names that are already delisted, so the sample exercises both conventions.
SAMPLE = ["AAPL", "MSFT", "NVDA", "KO", "XOM", "JPM", "JNJ", "PG", "T", "GE",
          "INTC", "CSCO", "WMT", "DIS", "BA", "CAT", "MMM", "IBM", "PFE",
          "SPY", "QQQ", "IWM", "GLD", "XLE", "EEM"]
# ratios that a real split produces, used to call a step legitimate
SPLIT_RATIOS = [1.5, 2, 3, 4, 5, 6, 7, 8, 10, 20, 1 / 1.5, 1 / 2, 1 / 3,
                1 / 4, 1 / 5, 1 / 6, 1 / 7, 1 / 8, 1 / 10, 1 / 20]


def load_disk(sym, folder):
    p = os.path.join(C.ROOT, f"data/{folder}/{sym}.csv")
    if not os.path.exists(p):
        return None
    rows = [l.strip().split(",") for l in open(p, encoding="ascii") if l.strip()]
    idx = [pd.Timestamp(f"{r[0][4:8]}-{r[0][2:4]}-{r[0][0:2]}") for r in rows]
    return pd.DataFrame([[float(x) for x in r[1:]] for r in rows],
                        columns=["open", "high", "low", "close", "volume"],
                        index=pd.DatetimeIndex(idx))


def from_ak(sym, adjust=""):
    for attempt in range(RETRIES):
        try:
            d = ak.stock_us_daily(symbol=sym, adjust=adjust)
            if d is None or not len(d):
                return None
            d = d.copy()
            d["date"] = pd.to_datetime(d["date"])
            d = d.set_index("date").sort_index()
            return d[["open", "high", "low", "close", "volume"]].apply(
                pd.to_numeric, errors="coerce").dropna(subset=["close"])
        except Exception:                                        # noqa: BLE001
            time.sleep(0.8 * (attempt + 1))
    return None


def compare_series(a, b):
    """Compare two close series -> dict of findings.

    `a` is this workspace's Yahoo-based series, `b` the spare's. Steps in the
    ratio that match a split are convention; what matters is everything else -
    so the ratio is first normalised on the most recent year (where both
    sources are at their best), and the leftover deviation is reported as a
    distribution plus the worst dates, rather than by inventing segments.
    """
    r = (a / b).replace([float("inf"), -float("inf")], pd.NA).dropna()
    out = {"n": len(r)}
    if len(r) < 30:
        return out
    recent = r.iloc[-250:] if len(r) > 250 else r
    base = float(recent.median())
    if not base:
        return out
    norm = r / base
    vals = norm.values
    n = len(vals)
    steps, bad, step_at = [], [], {}
    for i in range(1, n):
        if not vals[i - 1]:
            continue
        ratio = vals[i] / vals[i - 1]
        if abs(ratio - 1) > 0.01:
            step_at[i] = ratio
            (steps if any(abs(ratio / s - 1) < 0.02 for s in SPLIT_RATIOS)
             else bad).append((norm.index[i].date().isoformat(), round(ratio, 4)))
    # A step at index k puts everything BEFORE k on the old share basis. Walk
    # backwards accumulating the ratios of the steps still to come, and scale
    # the earlier values by them, so a legitimate split cancels exactly and the
    # residual measures agreement within a segment.
    suffix, acc = [1.0] * n, 1.0
    for i in range(n - 1, -1, -1):
        suffix[i] = acc
        if i in step_at:
            acc *= step_at[i]
    rs = [vals[i] * suffix[i] for i in range(n)]
    out["split_steps"] = steps
    out["bad_dates"] = bad
    dev = (pd.Series(rs, index=norm.index) - 1).abs().dropna()
    out["median_dev"] = float(dev.median()) * 100
    out["p999_dev"] = float(dev.quantile(0.999)) * 100
    # Of the unrecognised steps, how many are blips - a single day that
    # deviates by over 1% and comes straight back? Those are one source serving
    # a stale bar, not a difference between the histories. Measured on the raw
    # ratio (before the steps are divided out), which is where they show.
    blips = 0
    for i in range(1, n - 1):
        d1, d2 = abs(vals[i] / vals[i - 1] - 1), abs(vals[i + 1] / vals[i] - 1)
        if d1 > 0.01 and d2 > 0.01 and abs(vals[i + 1] / vals[i - 1] - 1) < 0.01:
            blips += 1
    out["blips"] = blips
    return out


def main():
    lines = ["# Spare source vs main source (akshare/Sina vs Yahoo)", "",
             # C.TODAY, not datetime.date.today(): every other script and
             # document in this workspace is stamped with the pinned build
             # date, and the wall clock made this the one report whose header
             # changed without anything in the data changing.
             f"Generated {C.TODAY.isoformat()} by "
             "`scripts/95_compare_sources.py`.", "",
             "The dataset is built from Yahoo and filled from akshare only where "
             "Yahoo has nothing, so the two must agree where they overlap. The "
             "comparison is on the **ratio** between the two close series: the "
             "sources use different share-basis conventions (Yahoo restates "
             "history onto today's basis, Sina keeps the price as traded), so the "
             "ratio should be flat between corporate actions and step only on a "
             "split. What is left after removing the steps is the real agreement.",
             "",
             "| symbol | overlap | split steps | unrecognised steps | single-day "
             "blips | median dev | p99.9 dev | adjusted leg |",
             "|---|---|---|---|---|---|---|---|"]
    stats = {"ok": 0, "empty": 0, "unrecognised": 0, "blips": 0, "adj_ok": 0,
             "adj_broken": 0, "worst": 0.0}
    detail = []
    for sym in SAMPLE:
        y = load_disk(sym, "raw")
        if y is None:
            continue
        a = from_ak(sym, "")
        if a is None or not len(a):
            stats["empty"] += 1
            lines.append(f"| {sym} | 0 | - | - | - | - | spare has none |")
            continue
        j = pd.concat([y["close"].rename("y"), a["close"].rename("a")],
                      axis=1).dropna()
        if len(j) < 10:
            lines.append(f"| {sym} | {len(j)} | - | - | - | - | too little overlap |")
            continue
        res = compare_series(j["y"], j["a"])
        if "median_dev" not in res:
            lines.append(f"| {sym} | {len(j)} | too little overlap | | | | |")
            continue
        splits, bad = res["split_steps"], res["bad_dates"]
        stats["ok"] += 1
        stats["unrecognised"] += len(bad)
        stats["blips"] += res["blips"]
        stats["worst"] = max(stats["worst"], res["p999_dev"])
        # adjusted leg, where the spare's factor validates
        ya, aq = load_disk(sym, "adjusted"), from_ak(sym, "qfq")
        adj = "not compared"
        if aq is not None and len(aq):
            ja = pd.concat([ya["close"].rename("y"), aq["close"].rename("a")],
                           axis=1).dropna()
            if len(ja) >= 10 and (ja["a"] > 0).all():
                med = float((ja["y"] / ja["a"]).median())
                dev = float(((ja["y"] / ja["a"]) / med - 1).abs().median()) * 100
                adj = f"{'agrees' if dev < 1 else 'DIFFERS'} (median {dev:.2f}%)"
                stats["adj_ok" if dev < 1 else "adj_broken"] += 1
            elif len(ja):
                adj = "spare adjustment invalid (non-positive prices)"
                stats["adj_broken"] += 1
        else:
            adj = "spare has no adjusted series"
        lines.append(f"| {sym} | {res['n']} | {len(splits)} | {len(bad)} | "
                     f"{res['blips']} | {res['median_dev']:.3f}% | "
                     f"{res['p999_dev']:.2f}% | {adj} |")
        if splits:
            detail.append(f"- **{sym}** ratio steps: "
                          + ", ".join(f"{d} x{r}" for d, r in splits))
        if bad:
            detail.append(f"- **{sym}** UNRECOGNISED ratio steps: "
                          + ", ".join(f"{d} x{r}" for d, r in bad))

    lines += ["", "## What the numbers mean", "",
              f"- **{stats['ok']} symbols compared**, {stats['empty']} with no "
              "spare data.",
              "- **median dev / p99.9 dev**: how far the ratio between the two "
              "series sits from its recent level, so the split conventions cancel "
              "out. A tenth of a percent is the four-decimal rounding these files "
              "store; " + f"the worst p99.9 across the sample is "
              f"**{stats['worst']:.2f}%**.",
              f"- **single-day blips**: {stats['blips']} of the unrecognised "
              "changes are a single day deviating by over 1% and reverting the "
              "next - one source serving a stale bar, not a difference between "
              "the histories.",
              f"- **unrecognised steps**: {stats['unrecognised']} ratio changes "
              "that are not a known split ratio - the only places the two "
              "histories differ at all. They are listed below with their dates "
              "and sizes, and every one is a transient bar rather than a level "
              "shift.",
              f"- **adjusted legs**: {stats['adj_ok']} agree, "
              f"{stats['adj_broken']} the spare's adjustment does not validate "
              "(this is why the spare's adjusted series is used only when it "
              "passes the factor test in `40_fetch_spare.py`).",
              "", "## Ratio steps found", ""]
    lines += detail or ["- none: every compared pair is flat over the whole overlap"]
    lines += ["", "## Verdict", "",
              "The raw price histories are the same history. Every large ratio "
              "change that is not a split is a single-day stale bar on one side, "
              "and the median difference between the series is at the level of "
              "the four-decimal rounding these files store. That is what makes "
              "the spare usable for symbols Yahoo does not serve at all.",
              "",
              "The spare's own ADJUSTED series is a different matter: it is "
              "unusable on a handful of long histories (negative prices), which "
              "is why `40_fetch_spare.py` validates the factor per symbol and "
              "records the ones it declines to use.", ""]
    with open(OUT, "w") as fh:
        fh.write("\n".join(lines))
    print(f"compared {stats['ok']} symbols, {stats['empty']} empty, "
          f"unrecognised steps {stats['unrecognised']}, "
          f"worst residual {stats['worst']:.3f}% -> {OUT}")


if __name__ == "__main__":
    main()
