import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""db_split_cn.py — move China market data into its own database.

Why CN gets its own file (user, 2026-10-01):

  * It shares almost nothing with the US tables — different venues (CZCE/SHFE/DCE/CFFEX/
    INE/GFEX), different columns per exchange, exchange-published IV and delta instead of
    one we solve, and a symbol namespace (CN:CZCE:MA) that collides with US tickers:
    'MA' is methanol there and Mastercard here, 'C' corn/Citigroup, 'MO' 中证1000/Altria.
  * Keeping CN inside market_history meant every US query had to remember to exclude it.
    One missed WHERE clause ranks methanol against Mastercard.
  * It unblocks the merge: CN rows can be repaired without holding the US database open.

Families it carries, as the user named them:
    CN futures · CN futures options · CN index options (CFFEX 中证1000 and peers)

TWO PHASES, TWO CONNECTIONS — this is the whole design.

The first version of this script did `src = dst`, aliasing one connection for both roles.
DuckDB then refused the attach ("already attached by database market_history"), a bare
`except: pass` hid it, and the copy silently became a no-op that still printed
"194,955 rows copied". Two roles, two connections, and no bare except around a step whose
failure would otherwise be invisible:

    phase 1  one connection to cn_market, market_history ATTACHED READ-ONLY  -> copy
    phase 2  one connection to market_history, opened read-write              -> delete

Phase 2 runs only if phase 1 verified every table's row count exactly.

    uv run python apps/data_ops/db_split_cn.py           # dry run
    uv run python apps/data_ops/db_split_cn.py --apply
"""
import argparse
import pathlib

import duckdb

ROOT = bootstrap.ROOT
SRC = ROOT / "market_history.duckdb"
DST = ROOT / "cn_market.duckdb"

TABLES = ["option_bars", "option_flow_daily", "iv_history", "underlying_daily",
          "option_bars_checks", "option_minutes"]

# The CN namespace: everything the akshare/cn loaders wrote.
CN_PRED = "(source LIKE 'akshare%' OR source LIKE 'cn[_]%')"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="delete from the source after every table verifies")
    args = ap.parse_args()

    # ── phase 1: copy, with the source attached read-only to the destination ──────────
    dst = duckdb.connect(str(DST))
    dst.execute(f"ATTACH '{SRC}' AS s (READ_ONLY)")
    present = {r[0] for r in dst.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name='s'").fetchall()}

    verified = {}
    for t in TABLES:
        if t not in present:
            continue
        n = dst.execute(f"SELECT count(*) FROM s.{t} WHERE {CN_PRED}").fetchone()[0]
        if not n:
            print(f"  {t:20} no CN rows")
            continue
        ddl = dst.execute("SELECT sql FROM duckdb_tables() "
                          "WHERE table_name=? AND database_name='s'", [t]).fetchone()[0]
        dst.execute(ddl.replace(f"CREATE TABLE {t}", f"CREATE TABLE IF NOT EXISTS {t}", 1))
        dst.execute(f"DELETE FROM {t} WHERE {CN_PRED}")      # idempotent re-run
        dst.execute(f"INSERT INTO {t} SELECT * FROM s.{t} WHERE {CN_PRED}")
        got = dst.execute(f"SELECT count(*) FROM {t} WHERE {CN_PRED}").fetchone()[0]
        verified[t] = (n, got)
        flag = "OK" if got == n else f"MISMATCH {got} != {n}"
        print(f"  {t:20} {n:>9,} CN rows -> {flag}")
    dst.execute("DETACH s")
    dst.close()

    bad = [t for t, (n, g) in verified.items() if n != g]
    total = sum(g for _, g in verified.values())
    print(f"\n  {total:,} CN rows in {DST.name}; {len(verified)} tables")
    if bad:
        print(f"  ABORTING phase 2 — these did not verify: {bad}")
        return

    # ── phase 2: delete, only now, and only from a connection that can write ───────────
    if not args.apply:
        print("  DRY RUN — re-run with --apply to remove the CN rows from market_history")
        return
    src = duckdb.connect(str(SRC))
    for t in verified:
        n = src.execute(f"SELECT count(*) FROM {t} WHERE {CN_PRED}").fetchone()[0]
        if n:
            src.execute(f"DELETE FROM {t} WHERE {CN_PRED}")
            left = src.execute(f"SELECT count(*) FROM {t} WHERE {CN_PRED}").fetchone()[0]
            print(f"  removed {n:>9,} CN rows from market_history.{t}"
                  f"{'' if left == 0 else f'  !! {left} REMAIN'}")
    src.close()
    print("  market_history is now US-only")


if __name__ == "__main__":
    main()
