#!/usr/bin/env python3
"""Backfill dividends and splits for every symbol, from Yahoo.

`data/corporate_actions/dividends.csv` and `splits.csv` are **inputs** to the
adjustment, and this is the only script that writes them. They hold whatever was
true when they were last built - 3,587 symbols of dividends and 2,447 of splits,
against 8,949 symbols in the tree.

That is not a cosmetic gap. The raw leg is Yahoo's series restated onto today's
share basis, so undoing the restatement needs the symbol's complete split
history; with ORKA's six splits missing, a legitimate series that ran from
$0.88 to $90 reads as one that ran from $479,001 to $90, and the price-scale
check flagged it as corrupt. Filling this file in is what makes that check mean
anything.

Download rule: never re-fetch what is already on disk. `symbols()` skips any symbol
with a row in either file, so a run reads the 4,400-odd symbols with no actions at
all. `--all` re-reads everything - a deliberate re-check, not a gap-fill.

The cost of skipping is real and stated where it bites: a symbol with SOME
dividends on file is skipped too, so a dividend added upstream after its row was
written is not picked up here. That residual is measurable rather than silent -
the corporate-actions section of 50_checklist.py recovers each dividend from the
adj/raw step in the prices and reports how many steps have no dividend behind
them.

Additive by construction: rows already on file are kept and Yahoo's are merged
in, deduped on (symbol, date), so a symbol Yahoo no longer serves keeps what it
has and a fresh Yahoo read can only add.

  python3 scripts/37_fetch_actions.py              # only symbols with none on file
  python3 scripts/37_fetch_actions.py AAPL MSFT    # just these
  python3 scripts/37_fetch_actions.py --all        # re-read every symbol

Writes data/corporate_actions/actions_backfill_log.csv with a line per symbol
(counts, or why nothing was read), then run 60 and 70 to rebuild.
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

DIV = os.path.join(C.CORP_ACTIONS, "dividends.csv")
SPL = os.path.join(C.CORP_ACTIONS, "splits.csv")
LOG = os.path.join(C.CORP_ACTIONS, "actions_backfill_log.csv")
BATCH = 40
WRITE_EVERY = 10   # batches between flushes; see flush()
CHUNK_SLEEP = 0.8


def symbols(args, only_missing=True):
    """Symbols to read, minus every one that already has actions on file.

    Re-reading a symbol whose actions are already held is a re-download of data
    this workspace has, and it already holds some for 3,587 symbols (dividends)
    and 2,447 (splits). Those are skipped by default and only --all reads them
    again, which is what a deliberate re-check looks like rather than a
    gap-fill. The cost of skipping is real and worth stating: a symbol that has
    SOME dividends on file is skipped too, so a dividend added upstream after
    its row was written will not be picked up here.
    """
    if args:
        return sorted(args)
    every = sorted(f[:-4] for f in os.listdir(C.ADJUSTED) if f.endswith(".csv"))
    if not only_missing:
        return every
    have = set()
    for path in (DIV, SPL):
        if os.path.exists(path):
            for r in csv.DictReader(open(path, encoding="utf-8")):
                if r.get("symbol"):
                    have.add(r["symbol"])
    return [s for s in every if s not in have]


def read_existing(path, value_col):
    """(set of (symbol, date), list of rows) already on file."""
    have, rows = set(), []
    if not os.path.exists(path):
        return have, rows
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if not r.get("symbol") or not r.get("date"):
            continue
        have.add((r["symbol"], r["date"]))
        rows.append([r["symbol"], r["date"], r[value_col]])
    return have, rows


def harvest(df, batch, out):
    """Pull non-zero Dividends / Stock Splits out of one yf.download result."""
    for sym in batch:
        try:
            sub = df[sym] if isinstance(df.columns, pd.MultiIndex) else df
        except (KeyError, TypeError):
            continue
        for col, sink in (("Dividends", out["div"]), ("Stock Splits", out["spl"])):
            if col not in sub:
                continue
            s = sub[col].dropna()
            for idx, v in s.items():
                v = float(v)
                if v and v == v:                    # non-zero and not NaN
                    sink.append((sym, idx.date().isoformat(), v))


def fetch(batch):
    """-> list of (symbol, date, value) per action type. Empty on failure."""
    out = {"div": [], "spl": []}
    df = None
    try:
        df = yf.download(batch, start=C.DATA_START.isoformat(),
                         auto_adjust=False, progress=False,
                         group_by="ticker", threads=True, timeout=90,
                         actions=True)
        if df is not None and len(df):
            harvest(df, batch, out)
    except Exception:                                        # noqa: BLE001
        df = None
    # The fallback is for a BATCH that failed, not for symbols that simply have
    # no dividends - most of the tree pays none, and asking Yahoo one symbol at
    # a time for all of them turns a 20-minute pass into hours.
    if df is not None and len(df):
        return out["div"], out["spl"]
    for s in batch:                      # one at a time, as a fallback
        try:
            t = yf.Ticker(s)
            for sp, sink in ((t.dividends, out["div"]), (t.splits, out["spl"])):
                if sp is None:
                    continue
                for idx, v in sp.dropna().items():
                    v = float(v)
                    if v and v == v:
                        sink.append((s, idx.date().isoformat(), v))
        except Exception:                                    # noqa: BLE001
            pass
        time.sleep(0.2)
    return out["div"], out["spl"]


def flush(rows_d, rows_s):
    """Write both files now -- every WRITE_EVERY batches, not only at the end. A sweep
    is ~5,700 symbols and twenty minutes; one killed at symbol 4,000 on 2026-10-06 threw
    away every request it had made.
    """
    for path, rows in ((DIV, rows_d), (SPL, rows_s)):
        rows.sort(key=lambda r: (r[0], r[1]))
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["symbol", "date", "amount" if path is DIV else "ratio"])
            w.writerows(rows)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    todo = symbols(args, only_missing="--all" not in sys.argv)
    if limit:
        todo = todo[:limit]
    print(f"symbols to read actions for: {len(todo)}"
          + ("  (every symbol - --all)" if "--all" in sys.argv
             else "  (only symbols with no actions on file)"), flush=True)

    have_d, rows_d = read_existing(DIV, "amount")
    have_s, rows_s = read_existing(SPL, "ratio")
    log, new_d, new_s = [], 0, 0
    t0 = time.time()
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        div, spl = fetch(batch)
        seen = {}
        for sym, d, v in div:
            seen[sym] = seen.get(sym, 0) + (1 if (sym, d) not in have_d else 0)
            if (sym, d) not in have_d:
                rows_d.append([sym, d, f"{v:.6f}"]); have_d.add((sym, d)); new_d += 1
        for sym, d, v in spl:
            if (sym, d) not in have_s:
                rows_s.append([sym, d, f"{v:g}"]); have_s.add((sym, d)); new_s += 1
        for sym in batch:
            log.append([sym, seen.get(sym, 0), len([1 for a, b, _ in spl if a == sym])])
        el = time.time() - t0
        print(f"  {min(i+BATCH,len(todo))}/{len(todo)}  +{new_d} dividends "
              f"+{new_s} splits  {el/60:.1f} min", flush=True)
        if (i // BATCH) % WRITE_EVERY == WRITE_EVERY - 1:
            flush(rows_d, rows_s)
        time.sleep(CHUNK_SLEEP)
    flush(rows_d, rows_s)
    # Cumulative, like the other logs here: a later pass records what it saw without
    # erasing what an earlier pass learned. Writing only this run's symbols would
    # throw away the record of the 4,423 read before it.
    history = {}
    if os.path.exists(LOG):
        for r in csv.DictReader(open(LOG, encoding="utf-8")):
            if r.get("symbol"):
                history[r["symbol"]] = (r["symbol"], r["new_dividends"],
                                        r["new_splits"])
    for row in log:
        history[row[0]] = tuple(str(x) for x in row)
    with open(LOG, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["symbol", "new_dividends", "new_splits"])
        w.writerows(sorted(history.values()))
    print(f"\nadded {new_d} dividends and {new_s} splits; "
          f"dividends.csv now {len(rows_d)} rows, splits.csv {len(rows_s)}")
    print("rebuild the database (60) and re-export (70) so the tree agrees again")


if __name__ == "__main__":
    main()
