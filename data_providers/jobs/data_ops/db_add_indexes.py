import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""db_add_indexes.py — persistent ART indexes for the hot per-ticker lookups.

Why: the "what do I already have?" query every downloader runs is
`WHERE source=? AND underlying=?` against option_bars (15.6M rows; its PK is
(source, symbol, day), which cannot serve an underlying lookup), and the option-bars
anchor path reads the warehouse with `WHERE symbol=?` against yahoo prices (35.5M rows,
no key at all). Without an index each of those is a full table scan — together they were
the read side of the 2026-10-03 disk storm.

DuckDB persists CREATE INDEX in the database file (verified on 1.5.6), so this is a
one-time build per file; re-running is a no-op. Run it while nothing holds the target
file: a reader blocks the writer, and without exclusivity the build is skipped with a
clear message rather than half-done.

    uv run python apps/data_ops/db_add_indexes.py [--dry-run]
"""
import argparse
import pathlib
import time

import duckdb

ROOT = bootstrap.ROOT

# (database, table, index name, columns). `underlying` serves both the scoped snapshot
# copy (`WHERE underlying IN (...)`) and the per-ticker coverage queries.
TARGETS = [
    ("market_history.duckdb", "option_bars", "idx_option_bars_underlying", "underlying"),
    ("market_data/yahoo.duckdb", "prices", "idx_prices_symbol", "symbol"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for rel, table, name, cols in TARGETS:
        path = ROOT / rel
        try:
            con = duckdb.connect(str(path))
        except Exception as e:
            # Held by another process (readers block writers). An index is an optimisation,
            # not a repair — skip loudly and re-run when the file is free.
            print(f"{rel}: SKIPPED — held by another process ({repr(e)[:70]})")
            continue
        try:
            have = {r[0] for r in con.execute(
                "SELECT index_name FROM duckdb_indexes()").fetchall()}
            if name in have:
                print(f"{rel}: {name} already present — nothing to do")
                continue
            n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if args.dry_run:
                print(f"{rel}: would create {name}({cols}) over {n:,} rows")
                continue
            # The build is a one-shot scan; give it room so it does not spill back onto the
            # same disk this exists to unburden.
            con.execute("SET memory_limit='6GB'")
            t = time.time()
            con.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({cols})")
            print(f"{rel}: {name}({cols}) built over {n:,} rows in {time.time() - t:.1f}s")
        except Exception as e:
            print(f"{rel}: FAILED — {repr(e)[:120]}")
        finally:
            con.close()


if __name__ == "__main__":
    main()
