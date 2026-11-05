import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""prune_us_daily_duplicates.py — delete US daily bars we already have in the warehouse.

The warehouse (market_data/yahoo.duckdb, 1980→, every ticker we trade) is the source of
truth for US daily EOD. `market_history.underlying_daily` keeps only what the warehouse
does NOT hold: CN symbols, symbols outside its universe, and the fresh tail since its
last build. This removes the second copy — it is storage hygiene, not deletion of data.

Re-runnable and safe: a row is deleted only when the warehouse holds the same symbol on
the same day, and only for US-market sources. Reports before/after per source.

    uv run python apps/data_ops/prune_us_daily_duplicates.py [--dry-run]
"""
import argparse
import pathlib
import sys

from data_providers.data import db  # noqa: E402

US_SOURCES = ("alpaca_stock", "yfinance_daily")
PREDICATE = """source IN ('alpaca_stock','yfinance_daily')
   AND EXISTS (SELECT 1 FROM yh.prices y
               WHERE y.symbol = mh.underlying_daily.symbol
                 AND y.date   = mh.underlying_daily.day)"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = db.connect(log=None, history="rw", universe=True)

    print("=== before ===")
    for src, n, syms, mn, mx in con.execute(f"""
            SELECT source, count(*), count(DISTINCT symbol), min(day), max(day)
            FROM mh.underlying_daily GROUP BY 1 ORDER BY 2 DESC""").fetchall():
        print(f"  {src:16} {n:>7,} rows  {syms:>4} syms  {mn} -> {mx}")

    dup = con.execute(f"SELECT count(*) FROM mh.underlying_daily WHERE {PREDICATE}").fetchone()[0]
    print(f"\nrows the warehouse already holds: {dup:,}")
    if args.dry_run:
        con.close()
        print("dry run — nothing deleted")
        return

    con.execute(f"DELETE FROM mh.underlying_daily WHERE {PREDICATE}")

    print("=== after ===")
    for src, n, syms, mn, mx in con.execute(f"""
            SELECT source, count(*), count(DISTINCT symbol), min(day), max(day)
            FROM mh.underlying_daily GROUP BY 1 ORDER BY 2 DESC""").fetchall():
        print(f"  {src:16} {n:>7,} rows  {syms:>4} syms  {mn} -> {mx}")
    kept = con.execute("""
            SELECT symbol, count(*), min(day), max(day) FROM mh.underlying_daily
            WHERE source IN ('alpaca_stock','yfinance_daily')
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10""").fetchall()
    print("\nkept US rows (the fresh tail past the warehouse's build):")
    for sym, n, mn, mx in kept:
        print(f"  {sym:8} {n:>5} rows  {mn} -> {mx}")
    con.close()
    print(f"\npruned {dup:,} duplicate rows")


if __name__ == "__main__":
    main()
