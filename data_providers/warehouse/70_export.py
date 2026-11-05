#!/usr/bin/env python3
"""Export from yahoo.duckdb: CSVs and stock-group .txt lists.

The database is the source of truth; these files are products of it.

Usage
-----
  python3 70_export.py --lists            stock-group lists  -> export/lists/
  python3 70_export.py --csv DIR          price CSVs         -> DIR/{adjusted,raw}/
  python3 70_export.py --verify [N]       regenerate N symbols from the DB and
                                          diff them against the shipped CSVs

Formatting is done in Python rather than SQL so the output is byte-identical to
the files the downloader wrote: DDMMYYYY,o,h,l,c,vol with 4 decimal places.
"""
import csv
import os
import random
import sys

import duckdb

from data_providers.warehouse import config as C

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)

DB = os.path.join(C.ROOT, "yahoo.duckdb")


def con():
    return duckdb.connect(DB, read_only=True)


def fmt_row(d, o, h, l, c, v):
    return (f"{d.day:02d}{d.month:02d}{d.year:04d},"
            f"{o:.4f},{h:.4f},{l:.4f},{c:.4f},{int(v or 0)}\n")


def write_prices(fh, rows):
    for d, o, h, l, c, v in rows:
        if o is None or c is None:
            continue                     # matches the downloader's drop rule
        fh.write(fmt_row(d, o, h, l, c, v))


# --------------------------------------------------------------------------
def export_lists():
    out = os.path.join(C.ROOT, "export", "lists")
    os.makedirs(out, exist_ok=True)
    c = con()
    total = 0
    for slug in C.INDEX_SLUGS:
        rows = c.execute(f"""
            SELECT symbol, data_start, data_end FROM dataset_{slug}
            WHERE symbol IN (SELECT symbol FROM symbols)
            ORDER BY symbol
        """).fetchall()
        # every index reads the SAME prices table - the lists share one store
        rows = [(s, a, b) for s, a, b in rows if a and b]
        p = os.path.join(out, f"{slug}.txt")
        with open(p, "w", encoding="ascii", newline="\r\n") as fh:
            for s, a, b in rows:
                fh.write(f"{s}{C.SUFFIX};{a.strftime('%d.%m.%Y')};"
                         f"{b.strftime('%d.%m.%Y')}\n")
        total += len(rows)
        print(f"  {slug:12} {len(rows):5} symbols -> {p}")

    rows = c.execute("""
        SELECT DISTINCT symbol, data_start, data_end FROM dataset
        ORDER BY symbol
    """).fetchall()
    p = os.path.join(out, "ALL_INDICES.txt")
    with open(p, "w", encoding="ascii", newline="\r\n") as fh:
        for s, a, b in rows:
            fh.write(f"{s}{C.SUFFIX};{a.strftime('%d.%m.%Y')};"
                     f"{b.strftime('%d.%m.%Y')}\n")
    print(f"  {'ALL_INDICES':12} {len(rows):5} symbols -> {p}")
    c.close()


def export_index_csv():
    """Per-index audit CSVs: usable constituents, and every exclusion + reason."""
    out = os.path.join(C.ROOT, "export", "index")
    os.makedirs(out, exist_ok=True)
    c = con()
    for slug in C.INDEX_SLUGS:
        rows = c.execute("""
            SELECT symbol, company, status, index_start, index_end,
                   data_start, data_end, bars, is_active
            FROM dataset_%s ORDER BY symbol""" % slug).fetchall()
        p = os.path.join(out, f"{slug}_constituents.csv")
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["symbol", "company", "status", "index_start", "index_end",
                        "data_start", "data_end", "bars", "is_active"])
            w.writerows(rows)
        ex = c.execute("""SELECT symbol, reason FROM index_excluded
                          WHERE index_slug=? ORDER BY symbol""", [slug]).fetchall()
        p2 = os.path.join(out, f"{slug}_excluded.csv")
        with open(p2, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["symbol", "reason"])
            w.writerows(ex)
        print(f"  {slug:12} {len(rows):5} usable, {len(ex):5} excluded -> {out}")
    c.close()


def export_csv(dest):
    adj_dir = os.path.join(dest, "adjusted")
    raw_dir = os.path.join(dest, "raw")
    os.makedirs(adj_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)
    c = con()
    syms = [r[0] for r in c.execute(
        "SELECT symbol FROM symbols ORDER BY symbol").fetchall()]
    for i, s in enumerate(syms, 1):
        rows = c.execute("""
            SELECT date, open, high, low, close, volume,
                   adj_open, adj_high, adj_low, adj_close
            FROM prices WHERE symbol=? ORDER BY date""", [s]).fetchall()
        with open(os.path.join(adj_dir, s + ".csv"), "w", encoding="ascii") as fh:
            write_prices(fh, [(r[0], r[6], r[7], r[8], r[9], r[5]) for r in rows])
        with open(os.path.join(raw_dir, s + ".csv"), "w", encoding="ascii") as fh:
            write_prices(fh, [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows])
        if i % 1000 == 0:
            print(f"  {i}/{len(syms)}", flush=True)
    c.close()
    print(f"  exported {len(syms)} symbols to {dest}")


def verify_symbols(c, sample, quiet=False):
    """Regenerate `sample` symbols from the DB and diff against the shipped CSVs.

    Returns the number of mismatched files. Used both by --verify and by the
    checklist, so 'the CSVs are reproducible from the database' is asserted in
    one place.
    """
    bad_adj = bad_raw = checked = 0
    for s in sample:
        rows = c.execute("""
            SELECT date, open, high, low, close, volume,
                   adj_open, adj_high, adj_low, adj_close
            FROM prices WHERE symbol=? ORDER BY date""", [s]).fetchall()
        for label, cols, folder, bad in (
                ("adjusted", (6, 7, 8, 9), C.ADJUSTED, "adj"),
                ("raw", (1, 2, 3, 4), C.RAW, "raw")):
            p = os.path.join(folder, s + ".csv")
            if not os.path.exists(p):
                continue
            gen = "".join(fmt_row(r[0], r[cols[0]], r[cols[1]],
                                  r[cols[2]], r[cols[3]], r[5]) for r in rows)
            if gen != open(p, encoding="ascii").read():
                if bad == "adj":
                    bad_adj += 1
                else:
                    bad_raw += 1
                if not quiet:
                    print(f"    MISMATCH {s} ({label})")
            checked += 1
    if not quiet:
        print(f"  verified {checked} files ({len(sample)} symbols): "
              f"adjusted mismatches={bad_adj}  raw mismatches={bad_raw}")
    return bad_adj + bad_raw


def verify(n):
    c = con()
    syms = [r[0] for r in c.execute(
        "SELECT symbol FROM symbols ORDER BY symbol").fetchall()]
    sample = syms if n <= 0 else random.Random(7).sample(syms, min(n, len(syms)))
    bad = verify_symbols(c, sample)
    c.close()
    return bad


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if "--lists" in args:
        export_lists()
    elif "--index-csv" in args:
        export_index_csv()
    elif "--csv" in args:
        export_csv(args[args.index("--csv") + 1])
    elif "--verify" in args:
        i = args.index("--verify")
        n = int(args[i + 1]) if len(args) > i + 1 else 200
        sys.exit(1 if verify(n) else 0)
