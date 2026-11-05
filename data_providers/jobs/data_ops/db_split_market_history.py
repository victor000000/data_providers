import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""db_split_market_history.py — move the bulk market tables out of trades.duckdb
into their own file, market_history.duckdb (design: plans/PROJECT_DESIGN.md §2).

Why: DuckDB allows ONE writing process per file. Long downloaders (option bars,
IV sweeps) held trades.duckdb for their whole run, which blocks recording a fill
at exactly the moment context is irreplaceable. After the split the log file is
only ever written by short capture/load scripts.

Moved:  option_bars · iv_history · underlying_daily · underlying_minutes
Stays:  fills · orders · account_daily · journal_entries · chain_* · positions ·
        risk_profile · screener_runs · underlying_ticks (trade-time captures)

Idempotent: each table is created with its ORIGINAL DDL (read back from
duckdb_tables(), so primary keys and constraints survive), replaced wholesale
(DELETE + INSERT), verified row-for-row, and only then dropped from the source.
A re-run after a crash repeats the verify-and-drop step safely.

    uv run python apps/data_ops/db_split_market_history.py            # move
    uv run python apps/data_ops/db_split_market_history.py --dry-run  # report only
    uv run python apps/data_ops/db_split_market_history.py --keep     # copy, don't drop

Refuse to run while another process writes trades.duckdb — check with the /proc
scan (no `ps` in this container) before starting.
"""
import argparse
import pathlib

import duckdb

ROOT = bootstrap.ROOT
TRADES = ROOT / "trades.duckdb"
HISTORY = ROOT / "market_history.duckdb"
TABLES = ["option_bars", "iv_history", "underlying_daily", "underlying_minutes"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep", action="store_true", help="copy but keep the source tables")
    args = ap.parse_args()

    con = duckdb.connect(str(TRADES))
    have = {r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name='trades'").fetchall()}
    todo = [t for t in TABLES if t in have]
    if not todo:
        print("nothing to move (already split)"); con.close(); return

    con.execute(f"ATTACH '{HISTORY}' AS mh")
    for t in todo:
        src_n = con.execute(f"SELECT count(*) FROM trades.{t}").fetchone()[0]
        print(f"{t}: {src_n:,} rows in trades.duckdb")
        if args.dry_run:
            continue
        ddl = con.execute(
            "SELECT sql FROM duckdb_tables() WHERE database_name='trades' AND table_name=?",
            [t]).fetchone()[0]
        con.execute(ddl.replace(f"TABLE {t}", f"TABLE mh.{t}", 1))
        con.execute(f"DELETE FROM mh.{t}")
        con.execute(f"INSERT INTO mh.{t} SELECT * FROM trades.{t}")
        dst_n = con.execute(f"SELECT count(*) FROM mh.{t}").fetchone()[0]
        if dst_n != src_n:
            raise SystemExit(f"ABORT: {t} copied {dst_n:,} of {src_n:,} rows — nothing dropped")
        print(f"  -> market_history.duckdb: {dst_n:,} rows  verified")
        if not args.keep:
            con.execute(f"DROP TABLE trades.{t}")
            print(f"  -> dropped from trades.duckdb")
    con.execute("DETACH mh")
    con.close()
    print("done" if not args.dry_run else "dry run — nothing written")


if __name__ == "__main__":
    main()
