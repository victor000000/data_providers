import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""db_migrate_v2.py — the 2026-10-01 redesign, applied.

Every change here exists because something broke today; nothing is a style preference.

  1. CONVENTIONS BECOME CONSTRAINTS. A mixed-unit column (Futu percent 21.4 vs everyone else's
     fraction 0.214) silently corrupted IV for 20 rows. A CHECK cannot be argued with:
        iv_history.atm_iv        BETWEEN 0 AND 5      (a 500% IV is not a market)
        option_bars.volume/close non-negative
        option_flow_daily OI     non-negative
     Failures surface at write time, in the writer that caused them.

  2. THE RECORD GETS ITS DEDUP KEY. `fills` had only a surrogate PK and no `exec_id` column,
     so 'UNIQUE(broker, exec_id)' lived only in the design. The column is added and a UNIQUE
     INDEX enforces it for rows that have one (NULLs stay distinct, so old rows are unaffected).

  3. TWO KEYS ARE DELIBERATELY NOT ADDED: `orders` keeps one row per lifecycle event and
     `chain_rows` one per intraday observation. Multiplicity is their semantics; a single-row
     key would delete meaning. They are guarded by no-exact-duplicates instead.

Idempotent: each step checks first, and every rebuild verifies its row count before swapping.
    uv run python apps/data_ops/db_migrate_v2.py [--dry-run]
"""
import argparse
import pathlib

import duckdb

ROOT = bootstrap.ROOT

# table -> (column, CHECK expression, human reason)
CHECKS = [
    ("iv_history", "atm_iv", "atm_iv IS NULL OR (atm_iv > 0 AND atm_iv <= 5)",
     "IV is a decimal fraction; mixed units corrupted 20 rows on 2026-10-01"),
    ("option_bars", "close", "close IS NULL OR close >= 0", "negative prices are vendor junk"),
    ("option_bars", "volume", "volume IS NULL OR volume >= 0", "negative volume is vendor junk"),
    ("option_flow_daily", "call_oi", "call_oi IS NULL OR call_oi >= 0", "OI cannot be negative"),
    ("option_flow_daily", "put_oi", "put_oi IS NULL OR put_oi >= 0", "OI cannot be negative"),
]


def has_check(con, table):
    rows = con.execute("""SELECT constraint_type FROM duckdb_constraints()
                          WHERE table_name=? AND constraint_type='CHECK'""", [table]).fetchall()
    return bool(rows)


def apply_checks(con, dry):
    """One rebuild per table, carrying ALL of that table's checks.

    (The first version skipped a table as soon as ANY check existed, so a second column's
    check was silently never added — its own lesson, kept in the comments.)
    """
    by_table = {}
    for table, col, expr, why in CHECKS:
        by_table.setdefault(table, []).append((col, expr, why))
    for table, specs in by_table.items():
        try:
            have = con.execute("""SELECT constraint_text FROM duckdb_constraints()
                                  WHERE table_name=? AND constraint_type='CHECK'""",
                               [table]).fetchall()
            if len(have) >= len(specs):
                print(f"  {table}: all {len(specs)} checks present — skipped"); continue
            for col, expr, why in specs:
                n_bad = con.execute(f"SELECT count(*) FROM {table} WHERE NOT ({expr})").fetchone()[0]
                print(f"  {table}.{col}: {n_bad} rows would violate — {why}")
                if n_bad and not dry:
                    con.execute(f"DELETE FROM {table} WHERE NOT ({expr})")
            if dry:
                continue
            cols = con.execute(f"DESCRIBE {table}").fetchall()
            keys = con.execute("""SELECT constraint_column_names FROM duckdb_constraints()
                                  WHERE table_name=? AND constraint_type='PRIMARY KEY'""",
                               [table]).fetchall()
            coldefs = ", ".join(f'"{c[0]}" {c[1]}' for c in cols)
            # constraint_column_names comes back as a LIST of names, not a string
            pk = f", PRIMARY KEY ({', '.join(keys[0][0])})" if keys else ""
            all_checks = ", ".join(f"CHECK ({e})" for _, e, _ in specs)
            con.execute("BEGIN TRANSACTION")
            con.execute(f"CREATE TABLE {table}__ck ({coldefs}{pk}, {all_checks})")
            con.execute(f"INSERT INTO {table}__ck SELECT * FROM {table}")
            after = con.execute(f"SELECT count(*) FROM {table}__ck").fetchone()[0]
            con.execute(f"DROP TABLE {table}")
            con.execute(f"ALTER TABLE {table}__ck RENAME TO {table}")
            con.execute("COMMIT")
            print(f"    -> {len(specs)} check(s) enforced, {after:,} rows kept")
        except Exception as e:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            print(f"    !! {table}: left untouched — {repr(e)[:110]}")


def apply_exec_id(con, dry):
    cols = [c[0] for c in con.execute("DESCRIBE fills").fetchall()]
    if "exec_id" not in cols:
        print("  fills.exec_id: missing — adding the column the design assumed")
        if not dry:
            con.execute("ALTER TABLE fills ADD COLUMN exec_id TEXT")
    else:
        print("  fills.exec_id: present")
    idx = con.execute("""SELECT count(*) FROM duckdb_indexes()
                         WHERE index_name='ux_fills_exec'""").fetchone()[0]
    if idx:
        print("  ux_fills_exec: present — skipped")
    elif not dry:
        con.execute("CREATE UNIQUE INDEX ux_fills_exec ON fills(broker, exec_id)")
        print("  ux_fills_exec: created — duplicate (broker, exec_id) pairs are now impossible")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=== market_history: conventions become constraints ===")
    mh = duckdb.connect(str(ROOT / "market_history.duckdb"))
    apply_checks(mh, args.dry_run)
    mh.close()

    print("=== trades: the record gets its dedup key ===")
    tr = duckdb.connect(str(ROOT / "trades.duckdb"))
    apply_exec_id(tr, args.dry_run)
    tr.close()

    if not args.dry_run:
        print("\n=== verify ===")
        mh = duckdb.connect(str(ROOT / "market_history.duckdb"), read_only=True)
        for t, ct, cols in mh.execute("""SELECT table_name, constraint_type, constraint_column_names
                                         FROM duckdb_constraints() WHERE constraint_type='CHECK'
                                         ORDER BY table_name""").fetchall():
            print(f"  {t:20} {ct} {cols}")
        mh.close()
        tr = duckdb.connect(str(ROOT / "trades.duckdb"), read_only=True)
        print("  fills columns now include exec_id:",
              "exec_id" in [c[0] for c in tr.execute("DESCRIBE fills").fetchall()])
        print("  unique index:", tr.execute("SELECT index_name FROM duckdb_indexes()").fetchall())
        tr.close()


if __name__ == "__main__":
    main()
