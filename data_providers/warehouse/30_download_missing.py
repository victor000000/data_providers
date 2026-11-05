#!/usr/bin/env python3
"""Fetch only the index constituents that are NOT already in data/.

Workspace download rule: never re-download what is already on disk. The set to
fetch is the membership files minus whatever data/adjusted already holds, MINUS
every symbol download_missing_log.csv records as already refused - so a re-run
asks for nothing it has asked for before and this is a no-op once the data is
complete. `--retry` overrides that and re-asks everything, which is a deliberate
question ("has Yahoo restored anything?") rather than a routine run.

The log is not decoration: without it, a pass over the 4,928 symbols still
missing spent 4,917 requests re-confirming answers already on file and recovered
one symbol - and that request volume is what gets an IP throttled, which then
appears as fresh failures on symbols that would have downloaded fine. See
scripts/96_verify_prices.py for the re-download-and-diff shape to use when the
goal is checking rather than filling.

Writes the same CSV format as the rest of the tree:
    DDMMYYYY,open,high,low,close,volume
    data/adjusted/<SYM>.csv   split+dividend adjusted  <- the dataset
    data/raw/<SYM>.csv        unadjusted               <- QC twin

Symbols Yahoo no longer serves (delisted, purged) are logged, not silently
dropped; the dataset builder reads that log to explain every exclusion.
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

BATCH = 40
CHUNK_SLEEP = 0.8
START = C.DATA_START.isoformat()
LOG = os.path.join(C.CORP_ACTIONS, "download_missing_log.csv")


def fmt_date(d):
    return f"{d.day:02d}{d.month:02d}{d.year:04d}"


def write_csv(path, df):
    with open(path, "w", encoding="ascii", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        for idx, row in df.iterrows():
            o, h, l, c, v = (row["Open"], row["High"], row["Low"],
                             row["Close"], row["Volume"])
            # All four, like 36_refetch_symbols.write_csv. Guarding only open and close
            # wrote a NaN high or low out as the literal text "nan"; 60_build_database
            # reads these files with ignore_errors=true, so the row disappeared from the
            # database while staying in the file.
            if pd.isna(o) or pd.isna(h) or pd.isna(l) or pd.isna(c):
                continue
            vol = 0 if pd.isna(v) else int(v)
            w.writerow([fmt_date(idx.date()), f"{o:.4f}", f"{h:.4f}",
                        f"{l:.4f}", f"{c:.4f}", vol])


def needed(retry=False):
    """Membership symbols with no data file yet, minus the ones already asked.

    The log is the record of what Yahoo was already asked for and could not
    serve. Ignoring it means every run re-asks all of it: a pass over the 4,928
    symbols still missing spent 4,917 requests re-confirming answers already on
    file and recovered one symbol. That is not merely slow - it is the request
    volume that gets an IP throttled, which then shows up as fresh failures on
    symbols that would have worked - and it contradicted this script's own
    promise that it is a no-op once the data is complete.

    --retry asks anyway, for when the question is whether Yahoo has restored
    something rather than whether the tree is complete.
    """
    have = {f[:-4] for f in os.listdir(C.ADJUSTED) if f.endswith(".csv")}
    want = set()
    for slug in C.INDEX_SLUGS:
        p = os.path.join(C.INDICES, slug, "membership.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p)):
            if r["symbol"] and r["symbol"] not in have:
                want.add(r["symbol"])
    if not retry and os.path.exists(LOG):
        tried = {r["symbol"] for r in csv.DictReader(open(LOG))
                 if r["status"] != "ok"}
        want -= tried
    return sorted(want)


def main():
    retry = "--retry" in sys.argv
    todo = needed(retry)
    print(f"missing symbols to fetch: {len(todo)}"
          + ("  (--retry: re-asking symbols Yahoo already failed on)"
             if retry else "  (symbols Yahoo has not already refused)"))
    if not todo:
        print("nothing to do - every missing symbol has already been asked for; "
              "use --retry to ask again")
        return
    os.makedirs(C.ADJUSTED, exist_ok=True)
    os.makedirs(C.RAW, exist_ok=True)

    log, done, t0 = [], 0, time.time()
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            raw = yf.download(chunk, start=START, auto_adjust=False,
                              progress=False, group_by="ticker",
                              threads=True, timeout=60, actions=True)
        except Exception as e:                                   # noqa: BLE001
            log.append((",".join(chunk), "batch error", str(e)[:90]))
            raw = None

        for s in chunk:
            try:
                df = raw[s] if raw is not None and len(chunk) > 1 else raw
            except Exception:                                    # noqa: BLE001
                df = None
            if df is None or len(df) == 0 or "Close" not in df:
                log.append((s, "unavailable", "Yahoo returned no data (delisted)"))
                continue
            df = df.dropna(subset=["Close"])
            if len(df) == 0:
                log.append((s, "unavailable", "all-NaN close"))
                continue
            df = df[~df.index.duplicated(keep="last")].sort_index()
            close = df["Close"]
            # Refuse to write a bar the database would reject. Yahoo serves
            # single-bar stubs with a zero open/close for some delisted symbols,
            # and its sub-penny prices round away at the four decimals these
            # files store: a low of 0.00004 is > 0 as a float and still lands in
            # the file as 0.0000. So the test is on the WRITTEN value
            # (config.MIN_PRICE), not on the float that arrived.
            adj = df["Adj Close"] if "Adj Close" in df else close
            f = (adj / close).where(close.notna() & (close != 0), 1.0)
            # The stored adjusted leg is raw * f, so it is f - not the raw
            # price - that decides whether the ADJUSTED file gets a 0.0000.
            # A long dividend history gives f well below 1, and a sub-penny raw
            # bar then rounds away on the way out even though the raw leg is
            # fine. Testing both legs keeps every writer on the rule
            # config.MIN_PRICE states: filter on the value being WRITTEN.
            valid = df[(df["Open"] >= C.MIN_PRICE) & (df["High"] >= C.MIN_PRICE)
                       & (df["Low"] >= C.MIN_PRICE)
                       & (df["Close"] >= C.MIN_PRICE)
                       & (df["Open"] * f >= C.MIN_PRICE)
                       & (df["High"] * f >= C.MIN_PRICE)
                       & (df["Low"] * f >= C.MIN_PRICE)
                       & (df["Close"] * f >= C.MIN_PRICE)]
            if valid.empty:
                log.append((s, "unavailable", "no bar with a positive price"))
                continue
            df = valid
            f = f[valid.index]
            write_csv(os.path.join(C.ADJUSTED, s + ".csv"), pd.DataFrame({
                "Open": df["Open"] * f, "High": df["High"] * f,
                "Low": df["Low"] * f, "Close": df["Close"] * f,
                "Volume": df["Volume"]}))
            write_csv(os.path.join(C.RAW, s + ".csv"), pd.DataFrame({
                "Open": df["Open"], "High": df["High"], "Low": df["Low"],
                "Close": df["Close"], "Volume": df["Volume"]}))
            log.append((s, "ok", f"{len(df)} rows "
                                 f"{df.index[0].date()}..{df.index[-1].date()}"))
            done += 1

        el = time.time() - t0
        print(f"  {min(i+BATCH,len(todo))}/{len(todo)}  ok={done}  "
              f"{done/max(el,1):.1f}/s  {el/60:.1f} min", flush=True)
        time.sleep(CHUNK_SLEEP)

    # Cumulative, like the logs 38 and 39 keep: a later pass records what it
    # saw without erasing what an earlier pass learned. This file is not only
    # an audit trail - 40_fetch_spare reads it to decide which symbols Yahoo
    # has already failed on and should be tried against the spare first - so
    # truncating it would silently reorder that run as well as lose the record
    # of the 6,895 symbols an earlier pass already proved unavailable.
    rows = {}
    if os.path.exists(LOG):
        for r in csv.DictReader(open(LOG)):
            rows[r["symbol"]] = (r["symbol"], r["status"], r["detail"])
    before = len(rows)
    for s, st, detail in log:
        rows[s] = (s, st, detail)          # this pass wins for what it touched
    with open(LOG, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["symbol", "status", "detail"])
        w.writerows(sorted(rows.values()))
    print(f"  log {before} earlier rows + {len(log)} this pass "
          f"-> {len(rows)} total")
    ok = sum(1 for _, st, _ in log if st == "ok")
    print(f"\nDONE fetched={ok}  unavailable={len(log)-ok}  -> {LOG}")


if __name__ == "__main__":
    main()
