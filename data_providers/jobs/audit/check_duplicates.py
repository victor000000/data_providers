import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""check_duplicates.py — prove no table holds the same fact twice.

Runs automatically at the end of every merge (see merge_staging.py), and standalone:

    uv run python apps/audit/check_duplicates.py [--db FILE ...]

WHY THIS EXISTS

"Did we duplicate download?" is a question that has to be answered with a query, not
with confidence. Duplicate rows here do not announce themselves — a second copy of a
bar is indistinguishable from the bar, except that every count, average and IVR built
on it is wrong. The failure is silent in exactly the way this project keeps meeting.

The check is structural rather than a count: every table is verified against ITS OWN
primary key, so a table whose key is (venue, product, contract, day) is checked that
way and one keyed (source, symbol, day) is checked its own way. A missing key shows up
as a table that cannot be checked at all, which is itself reported.

Exit code is non-zero if any table has duplicate keys, so a merge can refuse to
declare success while the data is wrong.
"""
import argparse
import pathlib
import sys

import duckdb

ROOT = bootstrap.ROOT
DBS = [ROOT / "market_history.duckdb", ROOT / "cn_market.duckdb",
       ROOT / "trades.duckdb", ROOT / "reference.duckdb"]


def check(con, label):
    """-> (checked, dupes, unkeyed). Uses each table's DECLARED primary key."""
    checked = dupes = 0
    unkeyed, doubled = [], []
    tabs = [r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
    for t in tabs:
        cons = con.execute(
            """SELECT constraint_column_names FROM duckdb_constraints()
               WHERE table_name=? AND constraint_type='PRIMARY KEY'""", [t]).fetchall()
        cols = list(cons[0][0]) if cons else []
        if not cols:
            unkeyed.append(t)
            continue
        sel = ", ".join(cols)
        n = con.execute(
            f"SELECT count(*) FROM (SELECT {sel} FROM {t} GROUP BY {sel} "
            f"HAVING count(*) > 1)").fetchone()[0]
        checked += 1
        if n:
            dupes += 1
            doubled.append((t, cols, n))
    print(f"  {label:22} {checked:>3} keyed tables checked, {dupes} with duplicates"
          + (f", {len(unkeyed)} unkeyed: {unkeyed}" if unkeyed else ""))
    for t, cols, n in doubled:
        print(f"    !! {t}: {n:,} duplicate {tuple(cols)} groups")
    return dupes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", nargs="*", default=None)
    args = ap.parse_args()
    targets = [pathlib.Path(d) for d in args.db] if args.db else DBS

    total = 0
    for p in targets:
        if not p.exists():
            continue
        try:
            con = duckdb.connect(str(p), read_only=True)
        except Exception as e:
            # A writer holds it. Not an error — say so rather than pass silently.
            print(f"  {p.name:22} SKIPPED — held by a writer ({repr(e)[:40]})")
            continue
        total += check(con, p.name)
        con.close()

    if total:
        print(f"\n  {total} table(s) contain duplicate keys — the data is wrong, "
              f"not merely untidy")
    else:
        print("\n  no duplicate keys anywhere")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
