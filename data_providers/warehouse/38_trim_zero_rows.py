#!/usr/bin/env python3
"""Drop bars with a zero price leg from the CSV tree, and record every removal.

Why this exists: the database treats a bar with a zero open/high/low/close as
unusable - a zero is a stub, not a price. The writers filter on the value they
are about to store (see config.MIN_PRICE), but files written before that rule
existed still carry such bars, so the CSVs and the database disagree and the
round-trip check in 50_checklist.py reports them.

Two shapes occur, and both are dropped:
  * a fully zero bar (ABBY trades at 0.0000 on all four legs) - the shell has
    stopped printing a price at all;
  * a zero low on a sub-penny name (ETEK: open/high/close 0.0001, low 0.0000) -
    the true low rounds away at the four decimals these files store.

Nothing is dropped silently: every removed row goes to
data/quarantine/zero_price_rows_removed.csv with the file it came from and the
zero legs it had, so the loss is auditable and reversible from a fresh download.
The log is cumulative - a later pass over newly downloaded files appends to it
rather than replacing the earlier record.
"""
import csv
import os
import sys

from data_providers.warehouse import config as C

LOG = os.path.join(C.QUARANTINE, "zero_price_rows_removed.csv")


def main():
    os.makedirs(C.QUARANTINE, exist_ok=True)
    removed = []
    touched = 0
    for folder in (C.ADJUSTED, C.RAW):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".csv"):
                continue
            path = os.path.join(folder, name)
            sym = name[:-4]
            kept, dropped = [], []
            for line in open(path, encoding="ascii"):
                s = line.strip()
                if not s:
                    continue
                p = s.split(",")
                if len(p) != 6:
                    dropped.append((s, "malformed"))
                    continue
                o, h, l, c = (float(x) for x in p[1:5])
                legs = [n for n, v in (("open", o), ("high", h),
                                       ("low", l), ("close", c)) if v == 0]
                if legs:
                    dropped.append((s, "+".join(legs)))
                else:
                    kept.append(s)
            if not kept:
                # Nothing left to store. Some OTC shells carry nothing but
                # sub-penny/zero bars, so every line is dropped and the file
                # would sit there empty - which breaks the "every price file is
                # in the database" property, because an empty file yields no
                # rows to load. Remove it: the symbol then reads as a member
                # with no usable data, which is exactly what it is, and
                # export/index/<index>_excluded.csv reports it with a reason.
                os.remove(path)
                touched += 1
                if dropped:
                    for row, why in dropped:
                        removed.append({
                            "symbol": sym,
                            "folder": os.path.basename(folder),
                            "row": row,
                            "zero_legs": why,
                        })
                else:
                    removed.append({
                        "symbol": sym,
                        "folder": os.path.basename(folder),
                        "row": "(file already empty)",
                        "zero_legs": "empty file removed",
                    })
                print(f"  {sym:9} {os.path.basename(folder):9} "
                      f"-{len(dropped):5} rows, file removed (nothing storable)")
                continue
            if not dropped:
                continue
            touched += 1
            for row, why in dropped:
                removed.append({
                    "symbol": sym,
                    "folder": os.path.basename(folder),
                    "row": row,
                    "zero_legs": why,
                })
            with open(path, "w", encoding="ascii") as fh:
                fh.write("".join(r + "\n" for r in kept))
            print(f"  {sym:9} {os.path.basename(folder):9} -{len(dropped):5} rows,"
                  f" {len(kept):6} left")

    if removed:
        # Merge with any earlier run rather than overwriting: the log is the
        # cumulative record of every bar this workspace has had to drop, and a
        # later pass over newly downloaded files must not erase it.
        prior = []
        if os.path.exists(LOG):
            prior = [r for r in csv.DictReader(open(LOG))
                     if (r["symbol"], r["folder"], r["row"]) not in
                     {(x["symbol"], x["folder"], x["row"]) for x in removed}]
        with open(LOG, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=[
                "symbol", "folder", "row", "zero_legs"])
            w.writeheader()
            w.writerows(prior + removed)
        print(f"  ({len(prior)} earlier removals kept in the log)")
    nbars = sum(1 for r in removed if not r["row"].startswith("("))
    nfiles = len({(r["symbol"], r["folder"]) for r in removed
                  if r["row"].startswith("(")})
    print(f"\nlog entries {len(removed)}: {nbars} bars dropped, "
          f"{nfiles} empty files removed -> {LOG}")
    print("rebuild the database (60) and re-export (70) so the tree agrees again")


if __name__ == "__main__":
    # `--help` must not be a write. None of these scripts takes an argument, so before
    # this guard `python 38_trim_zero_rows.py --help` ran the whole job: 39 rewrote the raw and
    # adjusted price files, 45 repaired or overwrote them, 60 deleted and rebuilt
    # yahoo.duckdb, 38 and the checkers rewrote their outputs. A flag that reads as
    # "tell me what this does" performing the destructive action is the same failure
    # the main repo hit on 2026-10-05, when a --help sweep rewrote `fills`; the guard
    # there is now shared/helpguard.py, and this is its shape without the import.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        sys.exit(0)
    main()
