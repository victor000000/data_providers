import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""db_add_log_keys.py — give the log tables the keys the design always claimed (defect D1).

DATABASE_DESIGN §2 documents `orders UNIQUE(source, order_id)`, `account_daily UNIQUE(day,
account, source)` and `journal_entries UNIQUE(source_file, row_num)` — and a recheck on
2026-10-01 found none of them existed, so dedup depended on each loader remembering to do it.

Pattern (same as db_restore_pks): create the table again with its full column list + key,
copy with SELECT DISTINCT (which collapses exact duplicate rows and nothing else), verify the
count, swap names inside one transaction. A key-duplicate whose non-key columns differ makes
the insert fail and the table is left untouched — loudly, never silently.

    uv run python apps/data_ops/db_add_log_keys.py [--dry-run]
"""
import argparse
import pathlib

import duckdb

ROOT = bootstrap.ROOT
TARGETS = [
    ("account_daily", "(day, account, source)"),
    ("orders", "(source, order_id)"),
    ("journal_entries", "(source_file, row_num)"),
    ("chain_rows", "(snap_id, strike, call_put)"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = duckdb.connect(str(ROOT / "trades.duckdb"))
    for table, key in TARGETS:
        try:
            have = con.execute("""SELECT constraint_type FROM duckdb_constraints()
                                  WHERE table_name=? AND constraint_type IN ('PRIMARY KEY','UNIQUE')""",
                               [table]).fetchall()
            if have:
                print(f"{table}: already keyed — skipped"); continue
            before = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            distinct = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT * FROM {table})").fetchone()[0]
            cols = con.execute(f"DESCRIBE {table}").fetchall()
            coldefs = ", ".join(f'"{c[0]}" {c[1]}' for c in cols)
            print(f"{table}: {before:,} rows, {before - distinct} exact duplicates")
            if args.dry_run:
                continue
            con.execute("BEGIN TRANSACTION")
            con.execute(f"CREATE TABLE {table}__new ({coldefs}, PRIMARY KEY {key})")
            con.execute(f"INSERT INTO {table}__new SELECT DISTINCT * FROM {table}")
            after = con.execute(f"SELECT count(*) FROM {table}__new").fetchone()[0]
            con.execute(f"DROP TABLE {table}")
            con.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
            con.execute("COMMIT")
            print(f"  -> key {key} added, {after:,} rows kept")
        except Exception as e:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            print(f"  !! {table}: left untouched — {repr(e)[:120]}")
    if not args.dry_run:
        print("\nkeys now:")
        for r in con.execute("""SELECT table_name, constraint_column_names FROM duckdb_constraints()
                                WHERE constraint_type='PRIMARY KEY' ORDER BY table_name""").fetchall():
            print(f"  {r[0]:18} {r[1]}")
    con.close()


if __name__ == "__main__":
    main()
