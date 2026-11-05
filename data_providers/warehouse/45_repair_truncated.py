#!/usr/bin/env python3
"""Repair files that hold only a recent slice of a long membership window.

Workspace download rule: never re-download what is already on disk. Only
candidates found by the coverage check are requested, and a replacement is
written only when the newly fetched series covers MORE of the membership window
than the file it would replace - so a stub can never overwrite a longer series,
and a symbol that is already fine is never asked for at all.

Found by the coverage check in 50_checklist.py: 87 index members had files
starting in July-September 2026 no matter what their window was - AVB held 27
bars for a 2007-2026 membership, BBBY 47 bars for 2017-2020, BEC 59 for
1990-2011. A file like that looks perfectly valid on its own; only measuring it
against the window the index says the company was a member for makes it
visible.

Each one is re-requested over the full range. If Yahoo returns the same stub
again - which it does for symbols whose history has been reduced on Yahoo's
side, AVB is one - the spare source is asked instead, so the two sources
together are what closes the gap. Every outcome is recorded:

  * Yahoo has the history   -> the file is rewritten in full;
  * only the spare has it   -> the file is written from the spare, with the
    spare's adjustment used only if it validates;
  * neither has it          -> the ticker string is a REUSED one whose earlier
    company no longer exists on either source (today's AAC is not 1990's AAC).
    The row is reported, because no download can fix it.

The log is CURRENT STATE, not cumulative: it is rewritten each run so that it
describes every member whose window is still under-covered and why. That is
deliberate - 50_checklist.py reads it as the list of explained shortfalls, so a
member that has since been repaired must leave it, or the checklist would treat
a fixed window as a known gap. (38, 39 and 40 keep cumulative logs instead,
because there the log is a record of loss rather than a list of gaps.)

Log: data/corporate_actions/truncated_files_repaired.csv

  python3 scripts/45_repair_truncated.py            # repair everything found
  python3 scripts/45_repair_truncated.py --dry-run  # just list them
"""
import csv
import datetime
import importlib
import os
import sys
import time

import pandas as pd
import yfinance as yf

from data_providers.warehouse import config as C
# the spare fetcher owns the akshare conventions and the adjustment validation;
# reusing it keeps one definition of "is this factor usable"
spare = importlib.import_module("data_providers.warehouse.40_fetch_spare")

LOG = os.path.join(C.CORP_ACTIONS, "truncated_files_repaired.csv")
# The signature of a truncated download is narrow: the file STARTS RECENTLY
# (within this many days of the newest bar in the dataset) and yet the
# membership window it has to cover began long before, so it covers less than
# half of it. Anything looser than this flags every reused 1990s ticker - whose
# file legitimately starts at the current company's listing - and every genuine
# recent IPO, of which the Russell lists have thousands.
RECENT_DAYS = 400
MIN_GAIN = 20        # a "repair" that adds a handful of bars is not one
MIN_WINDOW_DAYS = 400
MAX_COVERAGE = 0.5


def fmt_date(d):
    return f"{d.day:02d}{d.month:02d}{d.year:04d}"


def read_bars(path):
    if not os.path.exists(path):
        return []
    return [l.strip().split(",") for l in open(path, encoding="ascii") if l.strip()]


def bar_date(field):
    """The date of a price line, from its DDMMYYYY first field, or None.

    The files store DDMMYYYY ("29012008"). Three sites here needed that as a
    datetime.date and each open-coded the slice; two more needed it and instead
    formatted the OTHER side as YYYYMMDD and compared strings, which is not a date
    comparison in either direction: the formats do not match, and DDMMYYYY does not
    sort chronologically even against itself ("01022008" < "31012008"). Both of
    those comparisons are replaced by this.
    """
    if not field or len(field) != 8 or not field.isdigit():
        return None
    try:
        return datetime.date(int(field[4:8]), int(field[2:4]), int(field[0:2]))
    except ValueError:
        return None


def newest_bar():
    last = None
    for name in os.listdir(C.ADJUSTED):
        if not name.endswith(".csv"):
            continue
        try:
            with open(os.path.join(C.ADJUSTED, name), "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 64))
                tail = fh.read().decode("ascii", "ignore").strip().split("\n")[-1]
            d = tail.split(",")[0]
            ts = datetime.date(int(d[4:8]), int(d[2:4]), int(d[0:2]))
            last = ts if last is None else max(last, ts)
        except Exception:                                        # noqa: BLE001
            continue
    return last or C.TODAY


def suspects(newest):
    """Members whose file starts recently but must cover a much older window."""
    out = {}
    for slug in C.INDEX_SLUGS:
        p = os.path.join(C.INDICES, slug, "membership.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p, encoding="utf-8")):
            sym = r["symbol"]
            if not sym or sym in out:
                continue
            bars = read_bars(os.path.join(C.ADJUSTED, sym + ".csv"))
            if not bars:
                continue
            lo = (datetime.date.fromisoformat(r["start_date"])
                  if r["start_date"] else C.DATA_START)
            hi = (datetime.date.fromisoformat(r["end_date"])
                  if r["end_date"] else C.TODAY)
            # No minimum window length: the coverage check in 50_checklist.py
            # measures every window, so this must too, or a short window like
            # MTSR's (128 days, 8 bars held) is flagged by one and attempted by
            # neither.
            first = bars[0][0]
            fdate = datetime.date(int(first[4:8]), int(first[2:4]), int(first[0:2]))
            # BOTH conditions matter and each rules out a different thing:
            # a file that starts long ago but covers little of its window is a
            # VINTAGE BOUNDARY - the member's earlier company is simply not on
            # either source (55 windows look like this and cannot be fetched),
            # while a file that starts RECENTLY and still covers little is a
            # TRUNCATION, which is what this script exists to fix. Dropping the
            # first condition widens the list from ~160 to ~5,900 and turns the
            # repair into a re-download of the whole tree.
            # The SAME conditions 50_checklist.py uses, mirrored, so the two
            # cannot disagree about what a truncation is: the file must start
            # recently, and the window it must cover must have started well
            # before that. Testing the file against the WINDOW START instead
            # (the earlier form) skipped short windows like MTSR's entirely,
            # leaving the checklist flagging something nothing had attempted.
            recent_cut = newest - datetime.timedelta(days=RECENT_DAYS)
            if lo >= recent_cut:
                continue                  # the window itself is recent: an IPO
            if fdate < recent_cut:
                continue                  # file starts long ago: not truncation
            window_days = (min(hi, newest) - lo).days or 1
            # Was: `lo.isoformat().replace("-","") <= b[0] <= ...`, comparing a
            # YYYYMMDD bound against a DDMMYYYY field. The gate therefore never
            # measured coverage, so it selected symbols for the risky repair path on
            # the strength of a comparison that could not fail in the intended way.
            hi_d = min(hi, newest)
            covered = sum(1 for b in bars
                          if (lambda d: d is not None and lo <= d <= hi_d)(bar_date(b[0])))
            if covered / window_days >= MAX_COVERAGE:
                continue
            out[sym] = (slug, lo, hi, fdate, len(bars))
    return out


def fetch_full(sym):
    for attempt in range(3):
        try:
            df = yf.download([sym], start=C.DATA_START.isoformat(),
                             auto_adjust=False, progress=False,
                             group_by="ticker", threads=False,
                             timeout=90, actions=True)[sym]
            df = df.dropna(subset=["Close"])
            df = df[~df.index.duplicated(keep="last")].sort_index()
            if df.empty:
                return None
            keep = df[(df["Open"] >= C.MIN_PRICE) & (df["High"] >= C.MIN_PRICE)
                      & (df["Low"] >= C.MIN_PRICE) & (df["Close"] >= C.MIN_PRICE)]
            return keep if len(keep) else None
        except Exception:                                        # noqa: BLE001
            time.sleep(1.5 * (attempt + 1))
    return None


def write_leg(path, df, factor):
    """`factor` is a scalar, or a per-date Series (the adjustment factor)."""
    scalar = not hasattr(factor, "get")
    with open(path, "w", encoding="ascii", newline="") as fh:
        for idx, r in df.iterrows():
            k = float(factor) if scalar else float(factor.get(idx, 1.0))
            vals = [r["Open"] * k, r["High"] * k, r["Low"] * k, r["Close"] * k]
            if min(vals) < C.MIN_PRICE:
                continue
            v = 0 if pd.isna(r["Volume"]) else int(r["Volume"])
            fh.write(f"{fmt_date(idx.date())},{vals[0]:.4f},{vals[1]:.4f},"
                     f"{vals[2]:.4f},{vals[3]:.4f},{v}\n")


def covered_days(df, lo, hi):
    """How many bars of `df` fall inside [lo, hi] - the coverage measure used
    to decide whether a spare series is an improvement over what is stored."""
    if df is None or not len(df):
        return 0
    a, b = pd.Timestamp(lo), pd.Timestamp(min(hi, C.TODAY))
    return int(((df.index >= a) & (df.index <= b)).sum())


def old_covered(sym, lo, hi):
    """Same measure for the rows already on disk (read fresh, not from the
    suspect tuple, which carries only a count)."""
    # This is the guard that decides whether a spare series is allowed to REPLACE
    # what is on disk ("only when it covers more of the window"). It compared a
    # YYYYMMDD bound against the file's DDMMYYYY fields, so it returned only
    # accidental lexical matches: measured over 300 symbols it under-reported true
    # coverage by ~30x (HRS 9,877 real bars in window vs 326 counted), which would
    # let a spare holding a handful of window bars overwrite a file holding
    # thousands. Not yet fired -- every repaired row on record has after == before --
    # which is luck, not safety.
    hi_d = min(hi, C.TODAY)
    return sum(1 for b in read_bars(os.path.join(C.ADJUSTED, sym + ".csv"))
               if b and (lambda d: d is not None and lo <= d <= hi_d)(bar_date(b[0])))


def spare_full(sym, note):
    """Ask the spare source (akshare/Sina) for the whole history.

    Returns (frame in this workspace's column names, source label, note) or
    (None, "spare", note). The spare's own adjustment is validated exactly as
    in 40_fetch_spare; where it does not validate the raw series is used for
    both legs and the reason travels into the log.
    """
    raw, qfq, errored = spare.frames(sym)
    raw = spare.storable(raw)
    if raw is None:
        return None, "spare", note + ("; spare errored" if errored
                                      else "; spare has nothing")
    q = spare.storable(qfq) if qfq is not None else None
    good, why = spare.factor_ok(raw, q) if q is not None else \
        (False, "no adjusted series returned")
    out = raw.rename(columns={"open": "Open", "high": "High", "low": "Low",
                              "close": "Close", "volume": "Volume"})
    if good:
        aq = q.rename(columns={"open": "Open", "high": "High", "low": "Low",
                               "close": "Close", "volume": "Volume"})
        # reindex, not assign: a shorter adjusted series would otherwise align
        # by index and leave NaN in the gaps, which the writer turns into the
        # literal text "nan" (MTSR came out that way)
        out["Adj Close"] = aq["Close"].reindex(out.index).fillna(out["Close"])
        return out, "spare", note + f"; spare adjustment ok ({why})"
    out["Adj Close"] = out["Close"]
    return out, "spare", note + f"; spare adjustment unusable ({why})"


def main():
    dry = "--dry-run" in sys.argv
    newest = newest_bar()
    work = suspects(newest)
    print(f"members with a file too short for their window: {len(work)}",
          flush=True)
    rows, fixed, from_spare, hopeless = [], 0, 0, 0
    for n, (sym, (slug, lo, hi, fdate, bars)) in enumerate(sorted(work.items()), 1):
        if dry:
            print(f"  {sym:8} {bars:5} bars from {fdate}  window {lo}..{hi}  ({slug})")
            continue
        df = fetch_full(sym)
        old_n = bars
        source, note = "yahoo", ""
        if df is None or (len(df) <= old_n and df.index[0].date() >= fdate):
            # Yahoo holds no more than the stub already on disk. For several of
            # these the symbol's history has been reduced on Yahoo's side, so no
            # amount of re-requesting will recover it - ask the spare source.
            note = ("yahoo holds only the recent slice"
                    if df is not None else "yahoo returned nothing")
            df, source, note = spare_full(sym, note)
            # The spare is only allowed to REPLACE what is on disk when it
            # covers more of the window. Without this test a spare series that
            # is shorter than the stub - a different company under a reused
            # ticker - would overwrite it and lose bars.
            gain = covered_days(df, lo, hi) - old_covered(sym, lo, hi)
            if df is not None and gain < MIN_GAIN:
                df = None
                note = (note + f"; the spare adds only {max(gain,0)} bars of "
                               f"coverage (a stub is not a repair)")
        if df is None:
            hopeless += 1
            rows.append({"symbol": sym, "index_slug": slug, "before_bars": old_n,
                         "before_first": fdate.isoformat(), "after_bars": 0,
                         "after_first": "", "window_start": lo.isoformat(),
                         "result": f"no history on yahoo or the spare ({note})"})
        else:
            new_first = df.index[0].date()
            close = df["Close"]
            adj = df["Adj Close"] if "Adj Close" in df else close
            f = (adj / close).where(close.notna() & (close != 0), 1.0)
            write_leg(os.path.join(C.RAW, sym + ".csv"), df, 1.0)
            write_leg(os.path.join(C.ADJUSTED, sym + ".csv"), df, f)
            # Classify by the OUTCOME, not by which source answered. A fetch
            # that adds bars but still leaves the window barely covered has not
            # fixed anything (PCLN gained 1 bar of a 1990-2018 window and was
            # logged as "repaired", so the checklist kept flagging it as an
            # unexplained truncation while the log claimed it was handled).
            span = max(1, (min(hi, newest) - lo).days)
            new_cov = covered_days(df, lo, hi) / span
            if new_cov < MAX_COVERAGE:
                rows.append({"symbol": sym, "index_slug": slug,
                             "before_bars": old_n,
                             "before_first": fdate.isoformat(),
                             "after_bars": len(df),
                             "after_first": new_first.isoformat(),
                             "window_start": lo.isoformat(),
                             "result": f"not fixed: the best series available "
                                       f"covers only {new_cov:.0%} of the window "
                                       f"({source}; {note})"})
                hopeless += 1
            else:
                if source == "yahoo":
                    fixed += 1
                else:
                    from_spare += 1
                rows.append({"symbol": sym, "index_slug": slug,
                             "before_bars": old_n,
                             "before_first": fdate.isoformat(),
                             "after_bars": len(df),
                             "after_first": new_first.isoformat(),
                             "window_start": lo.isoformat(),
                             "result": f"repaired from {source} ({note})" if note
                                       else f"repaired from {source}"})
            print(f"  {sym:8} {old_n:5} -> {len(df):5} bars ({new_first}..) "
                  f"[{source}]", flush=True)
        if n % 10 == 0:
            print(f"  {n}/{len(work)}  yahoo={fixed} spare={from_spare} "
                  f"none={hopeless}", flush=True)
        time.sleep(0.4)

    if not dry:
        os.makedirs(C.CORP_ACTIONS, exist_ok=True)
        with open(LOG, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=[
                "symbol", "index_slug", "before_bars", "before_first",
                "after_bars", "after_first", "window_start", "result"])
            w.writeheader()
            w.writerows(rows)
        print(f"\nrepaired {fixed} from Yahoo, {from_spare} from the spare, "
              f"{hopeless} with no history anywhere -> {LOG}")
        print("rebuild the database (60) and re-export (70)")


if __name__ == "__main__":
    # `--help` must not be a write. None of these scripts takes an argument, so before
    # this guard `python 45_repair_truncated.py --help` ran the whole job: 39 rewrote the raw and
    # adjusted price files, 45 repaired or overwrote them, 60 deleted and rebuilt
    # yahoo.duckdb, 38 and the checkers rewrote their outputs. A flag that reads as
    # "tell me what this does" performing the destructive action is the same failure
    # the main repo hit on 2026-10-05, when a --help sweep rewrote `fills`; the guard
    # there is now shared/helpguard.py, and this is its shape without the import.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        sys.exit(0)
    main()
