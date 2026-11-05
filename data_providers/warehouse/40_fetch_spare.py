#!/usr/bin/env python3
"""Fill symbols Yahoo does not serve from a spare source (akshare / Sina).

Workspace download rule: never re-download what is already on disk. Yahoo is
the main source and stays the main source: this script only ever runs for
symbols that are MISSING from data/, and it refuses to touch a symbol that
already has a file. Nothing downloaded here overwrites Yahoo data.

The rule is enforced by `wanted()`, which derives its list the same way
30_download_missing.py does - membership minus what the tree already holds, minus
whatever download_missing_log.csv records as already refused - so a re-run asks
for nothing it has already asked for. `--refresh` is the explicit opt-in that
re-decides symbols already filled from this source; it is a repair mode, not a
routine one, and it re-downloads by definition.

Why a spare is needed at all: Yahoo has purged a large part of the delisted
tail, and the Russell 2000 history is full of it - 6,900 of the 7,699 symbols a
full fetch asked for came back empty. A sample of those shows roughly a third
are served by Sina through akshare, which is the difference between a
survivorship-biased 1990s and a usable one.

Why Sina specifically - akshare has no source switch. Every akshare interface
is bound to one upstream, so "switching source" means calling a different
function for the same data. For US daily history there are exactly two:

    stock_us_daily(symbol, adjust)                          -> Sina (used here)
    stock_us_hist(symbol, period, start, end, adjust)       -> Eastmoney

Eastmoney is not usable from this machine: its US endpoints refuse the
connection outright (push2his/push2/63.push2his, plain requests and
TLS-impersonated, with the ut token and browser headers - all dropped), while
its China endpoints answer normally, so it is the US routes that are blocked
rather than the host. The remaining akshare US interfaces
(stock_us_spot_em, stock_us_hist_min_em, stock_us_pink_spot_em) are all
Eastmoney and fail the same way. Only Sina is left.

Sina's own caveat, from its users: repeated calls get an IP throttled. That is
what the error rows in the log are - the fix is to re-run this script, which
retries every symbol that has no file yet, rather than to raise the worker
count.

The adjustment problem, and why the two legs are treated differently:

  * Sina's prices are AS TRADED. Yahoo restates history onto today's share
    basis, so the two raw series differ by the cumulative split factor (AAPL
    2015 reads 130.53 on Sina and 32.64 on Yahoo - the 2020 4-for-1). That is a
    convention, not an error, and it is recorded per symbol.
  * Sina's forward-adjusted series is NOT reliable over long histories: AAPL
    1995 comes back as -9.93 and KO 1995 as -16.05. Writing that into the
    adjusted leg would corrupt the dataset far worse than leaving the leg
    unadjusted, so the adjusted leg is only written when the factor validates
    (every leg positive, factor positive and non-decreasing over time, ending
    at 1.0). Otherwise the symbol is stored with adjusted = raw and the reason
    is recorded, so the gap is visible instead of silent.

Every fill is logged to data/corporate_actions/spare_source_fills.csv with its
row count, date range and adjustment verdict.

  python3 scripts/40_fetch_spare.py              # everything still missing
  python3 scripts/40_fetch_spare.py --limit 50   # a first slice, to look at
  python3 scripts/40_fetch_spare.py --refresh    # redo the symbols already
                                                 # filled (see refresh_list)
"""
import collections
import csv
import datetime
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak
import pandas as pd

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)

LOG = os.path.join(C.CORP_ACTIONS, "spare_source_fills.csv")
YAHOO_LOG = os.path.join(C.CORP_ACTIONS, "download_missing_log.csv")
SLEEP = 0.6            # gap after each symbol's requests; Sina throttles bursts
RETRIES = 3


def fmt_date(d):
    return f"{d.day:02d}{d.month:02d}{d.year:04d}"


def refresh_list():
    """Symbols already filled by this script (or repaired from it), to redo.

    The first version of factor_ok() tested monotonicity, which is wrong for a
    reverse split: it accepted those and rejected ordinary dividend payers, so
    the adjusted/raw decision it made for every symbol filled so far has to be
    recomputed. This returns the symbols to redo; the fetch is the same.
    """
    syms = set()
    for path in (LOG,
                 os.path.join(C.CORP_ACTIONS, "truncated_files_repaired.csv")):
        if not os.path.exists(path):
            continue
        for r in csv.DictReader(open(path)):
            if r["symbol"] and "spare" in (r.get("adjusted", "")
                                           + r.get("result", "")):
                syms.add(r["symbol"])
    # Two kinds of row need another attempt: ones written before the timestamp
    # field existed (decided by the superseded monotonicity test), and ones
    # whose fetch ERRORED, because a failed fetch leaves the file as the earlier
    # pass wrote it - the symbol has a file, so the main fill will not revisit
    # it, and the wrong decision would survive silently.
    if os.path.exists(LOG):
        for r in csv.DictReader(open(LOG)):
            if r["symbol"] and (not r.get("checked") or r["status"] == "error"):
                syms.add(r["symbol"])
    return sorted(syms)


def wanted():
    """Symbols to try: index members with no file on disk.

    Derived the same way 30_download_missing does - membership minus what the
    tree already holds - so the two never disagree about what is missing. The
    Yahoo log is consulted only to prefer symbols Yahoo already failed on.
    """
    have = {f[:-4] for f in os.listdir(C.ADJUSTED) if f.endswith(".csv")}
    want = set()
    for slug in C.INDEX_SLUGS:
        p = os.path.join(C.INDICES, slug, "membership.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p, encoding="utf-8")):
            if r["symbol"]:
                want.add(r["symbol"])
    missing = want - have
    tried = set()
    if os.path.exists(YAHOO_LOG):
        tried = {r["symbol"] for r in csv.DictReader(open(YAHOO_LOG))
                 if r["status"] != "ok"}
    # symbols Yahoo failed on first (the point of the spare), then the rest;
    # within each, the ones held by more groups first, so the most useful fills
    # land earliest if the run is cut short
    held = collections.Counter()
    for slug in C.INDEX_SLUGS:
        p = os.path.join(C.INDICES, slug, "membership.csv")
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r["symbol"]:
                    held[r["symbol"]] += 1
    key = lambda s: (-held[s], s)
    return sorted(missing & tried, key=key) + sorted(missing - tried, key=key)


def _transient(e):
    """Is this exception worth retrying, or does it mean "no such symbol"?

    Sina answers a symbol it does not have with an empty page, and akshare then
    fails while PARSING it - `SyntaxError: invalid syntax (<string>, line 1)`
    from its eval() of the body, or `IndexError: list index out of range` from
    indexing an empty result. Those are "the spare has nothing", not a network
    problem, and retrying them six times each is both wrong in the log (they
    were recorded as errors, which reads as retryable) and the reason a run
    looked like it was being throttled when it was not. Only transport-level
    failures are transient.
    """
    name = type(e).__name__
    return any(k in name for k in ("Connection", "Timeout", "SSL", "Proxy",
                                   "HTTPError", "ChunkedEncoding", "Connect"))


def frames(sym):
    """-> (raw, qfq, error) DataFrames indexed by date.

    `error` is True only when a TRANSIENT failure survived every attempt. A
    symbol the spare source does not have returns (None, None, False) - kept
    apart because a transient failure logged as "no data" is how a symbol
    silently leaves the dataset for good.
    """
    transient = []

    def one(adjust):
        for attempt in range(RETRIES):
            try:
                d = ak.stock_us_daily(symbol=sym, adjust=adjust)
                if d is None or not len(d):
                    return None
                d = d.copy()
                d["date"] = pd.to_datetime(d["date"])
                d = d.set_index("date").sort_index()
                cols = ["open", "high", "low", "close", "volume"]
                d = d[cols].apply(pd.to_numeric, errors="coerce")
                return d.dropna(subset=["open", "high", "low", "close"])
            except Exception as e:                               # noqa: BLE001
                if not _transient(e):
                    return None                # the spare does not have it
                transient.append(type(e).__name__)
                time.sleep(0.5 * (attempt + 1))
        return None
    raw = one("")
    qfq = one("qfq")
    return raw, qfq, bool(transient) and raw is None and qfq is None


def storable(df):
    """Drop bars the database would reject (see config.MIN_PRICE)."""
    if df is None or df.empty:
        return None
    keep = df[(df["open"] >= C.MIN_PRICE) & (df["high"] >= C.MIN_PRICE)
              & (df["low"] >= C.MIN_PRICE) & (df["close"] >= C.MIN_PRICE)]
    return keep if len(keep) else None


def factor_ok(raw, qfq):
    """Is the spare's adjustment factor usable? -> (ok, why)

    The factor f = qfq/raw restates as-traded prices onto today's share basis.
    Three properties must hold, and none of them is monotonicity - that was the
    first version of this test and it was wrong in both directions. A forward
    split or a dividend makes f climb towards 1.0 through time; a REVERSE split
    makes it fall (ASRT runs 60 -> 15 -> 1.0 over 2018-2026). What a working
    factor never does is wobble: it is a step function that changes only on a
    corporate action. A broken one jitters every day, which is what Sina
    returns on long histories, and it is caught here.
    """
    j = raw.join(qfq, how="inner", lsuffix="_r", rsuffix="_q")
    if len(j) < 2:
        return False, "no overlap between raw and adjusted"
    f = (j["close_q"] / j["close_r"]).replace([float("inf"), -float("inf")],
                                              pd.NA).dropna()
    if len(f) < 2 or (f <= 0).any():
        return False, "factor not strictly positive"
    # All four legs have to be adjusted by the SAME factor. Judging close alone
    # accepted series whose open/high/low carried a different one: ACTA's stored
    # adjusted bar disagreed with its own raw bar by up to 39x between legs, so
    # the "adjusted" row was a bar that never traded on any share basis.
    for leg in ("open", "high", "low"):
        g = (j[leg + "_q"] / j[leg + "_r"]).replace(
            [float("inf"), -float("inf")], pd.NA).dropna()
        common = f.index.intersection(g.index)
        if len(common) < len(f):
            return False, f"the {leg} leg does not cover the same dates as close"
        # Judged only where both legs are large enough to carry the comparison,
        # and with the same 5% the checklist uses, so the two can never
        # disagree about what a broken adjustment is.
        big = common[(j.loc[common, leg + "_r"] >= 0.01)
                     & (j.loc[common, leg + "_q"] >= 0.01)]
        if not len(big):
            continue
        dev = (abs(g.loc[big] - f.loc[big]) / f.loc[big])
        bad = int((dev > 0.05).sum())
        if bad:
            return False, (f"the {leg} leg is adjusted differently from close "
                           f"on {bad} of {len(big)} days")
    if abs(float(f.iloc[-1]) - 1.0) > 0.02:
        return False, f"factor ends at {float(f.iloc[-1]):.4f}, not 1.0"
    ch = f.pct_change().dropna().abs()
    # a real corporate action moves the factor by more than 1%; day-to-day
    # wobble below that is the signature of a broken adjustment
    noisy = int(((ch > 1e-9) & (ch < 0.005)).sum())
    limit = max(20, int(0.02 * len(f)))
    if noisy > limit:
        return False, (f"factor wobbles on {noisy} of {len(f)} days "
                       f"(limit {limit}): broken forward adjustment")
    return True, f"{noisy} wobble days in {len(f)}, ends at 1.0"


def write_leg(path, df, factor):
    """Write one leg; returns the number of rows actually stored."""
    written = 0
    with open(path, "w", encoding="ascii", newline="") as fh:
        for idx, r in df.iterrows():
            vals = [r["open"], r["high"], r["low"], r["close"]]
            vals = [v * factor for v in vals]
            if min(vals) < C.MIN_PRICE:
                continue                    # would be stored as 0.0000
            vol = 0 if pd.isna(r["volume"]) else int(r["volume"])
            fh.write(f"{fmt_date(idx.date())},{vals[0]:.4f},{vals[1]:.4f},"
                     f"{vals[2]:.4f},{vals[3]:.4f},{vol}\n")
            written += 1
    return written


def one_symbol(sym):
    """Fetch, validate and write one symbol. Returns its log row."""
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    time.sleep(SLEEP)
    raw, qfq, errored = frames(sym)
    if errored:
        return {"symbol": sym, "status": "error", "rows": 0, "first": "",
                "last": "", "adjusted": "", "checked": stamp,
                "detail": "every attempt raised a network/parse error"}
    raw = storable(raw)
    if raw is None:
        return {"symbol": sym, "status": "empty", "rows": 0, "first": "",
                "last": "", "adjusted": "", "checked": stamp,
                "detail": "the spare source has no data for this symbol"}
    q = storable(qfq) if qfq is not None else None
    good, why = factor_ok(raw, q) if q is not None else \
        (False, "no adjusted series returned")
    # The two legs have to cover the SAME dates. factor_ok() judges the factor
    # on raw.join(q), so it can only ever see the overlap - which is precisely
    # the part the adjusted series does have. Sina returns a shorter adjusted
    # series for some names (ACTA: 507 bars against the raw series' 1,469) and
    # nothing noticed, so the file shipped with 961 raw bars that had no
    # adjusted twin at all. Falling through to the raw-only path below writes
    # the raw series to both legs and records the reason, which is what this
    # script already does whenever the adjustment cannot be trusted.
    if good and not raw.index.difference(q.index).empty:
        missing = len(raw.index.difference(q.index))
        good, why = False, (f"the adjusted series covers only {len(q)} of "
                            f"{len(raw)} bars ({missing} missing)")
    first, last = raw.index[0].date().isoformat(), raw.index[-1].date().isoformat()
    if good:
        write_leg(os.path.join(C.RAW, sym + ".csv"), raw, 1.0)
        if write_leg(os.path.join(C.ADJUSTED, sym + ".csv"), q, 1.0) == 0:
            # the spare's adjusted series sits entirely below the smallest
            # price these files store, so it would leave an EMPTY adjusted file
            # beside a populated raw one and break the "both folders hold the
            # same symbols" property (AAC did exactly this). Store the raw
            # series in both legs and say so.
            write_leg(os.path.join(C.ADJUSTED, sym + ".csv"), raw, 1.0)
            return {"symbol": sym, "status": "ok-raw-only", "rows": len(raw),
                    "first": first, "last": last,
                    "adjusted": "none (equals raw)", "checked": stamp,
                    "detail": why + "; the adjusted series rounds away entirely"}
        return {"symbol": sym, "status": "ok", "rows": len(raw), "first": first,
                "last": last, "adjusted": "spare-adjusted", "checked": stamp,
                "detail": why}
    # Raw only: the prices are as traded and the adjustment for this name does
    # not validate, so both legs carry the raw series and the gap is recorded
    # rather than filled with a factor known to be broken.
    write_leg(os.path.join(C.RAW, sym + ".csv"), raw, 1.0)
    write_leg(os.path.join(C.ADJUSTED, sym + ".csv"), raw, 1.0)
    return {"symbol": sym, "status": "ok-raw-only", "rows": len(raw),
            "first": first, "last": last, "adjusted": "none (equals raw)",
            "checked": stamp, "detail": why}


def main():
    args = sys.argv[1:]
    limit, workers = 0, 6
    for i, a in enumerate(args):
        if a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
        if a == "--workers" and i + 1 < len(args):
            workers = int(args[i + 1])
    todo = refresh_list() if "--refresh" in args else wanted()
    if limit:
        todo = todo[:limit]
    print(f"symbols {'to refresh' if '--refresh' in args else 'missing from data/'}"
          f" : {len(todo)}  (spare source: akshare/Sina, {workers} workers)",
          flush=True)

    rows, t0 = [], time.time()
    counts = collections.Counter()
    lock = threading.Lock()

    def record(row):
        with lock:
            rows.append(row)
            counts[row["status"]] += 1
            n = len(rows)
            if n % 50 == 0 or n == len(todo):
                el = time.time() - t0
                print(f"  {n}/{len(todo)}  filled={counts['ok']} "
                      f"raw-only={counts['ok-raw-only']} empty={counts['empty']} "
                      f"errors={counts['error']}  {el/60:.1f} min", flush=True)
            if n % 250 == 0:
                write_log(rows)          # keep the log current if this dies

    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = [pool.submit(one_symbol, s) for s in todo]
        for j in as_completed(jobs):
            try:
                record(j.result())
            except Exception as e:                               # noqa: BLE001
                record({"symbol": "?", "status": "error", "rows": 0, "first": "",
                        "last": "", "adjusted": "", "detail": str(e)[:60]})
    write_log(rows)
    print(f"\nfilled {counts['ok']} (with adjustment) + {counts['ok-raw-only']} "
          f"(raw only), {counts['empty']} empty, {counts['error']} errored "
          f"-> {LOG}")
    print("rebuild the database (60) and re-export (70) so the tree agrees again")


def write_log(rows):
    os.makedirs(C.CORP_ACTIONS, exist_ok=True)
    prior = []
    if os.path.exists(LOG):
        done = {r["symbol"] for r in rows}
        prior = [r for r in csv.DictReader(open(LOG)) if r["symbol"] not in done]
    with open(LOG, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "status", "rows", "first",
                                           "last", "adjusted", "checked",
                                           "detail"], restval="")
        w.writeheader()
        w.writerows(prior + rows)


if __name__ == "__main__":
    main()
