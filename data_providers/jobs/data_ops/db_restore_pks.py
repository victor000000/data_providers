import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

# ── --help guard ─────────────────────────────────────────────────────────────
# This script WRITES to a database and takes no arguments, so `script.py --help`
# used to execute it in full. Not hypothetical: a 2026-10-05 sweep ran every app
# with --help and rewrote the fills table (via seed_fills.py) and risk_profile.
# A read-only-looking flag must not be a write. Exits before any connection.
from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap below, and a
# docstring must be the first statement, so all 33 copies printed "(no docstring)".

"""db_restore_pks.py — restore the primary keys the market_history split dropped.

Found live on 2026-09-30: every option-bar insert failed with
`Binder Error: There are no UNIQUE/PRIMARY KEY ...` — the split re-created the tables from
`duckdb_tables().sql`, which does not carry the inline PRIMARY KEY, so `ON CONFLICT DO
NOTHING` (our whole dedup guarantee) had nothing to bind to.

This rebuilds each affected table with its key: create with the full column list + PRIMARY
KEY, copy with SELECT DISTINCT (the key also de-duplicates anything that slipped in while
the constraint was missing), swap names inside one transaction. Idempotent: a table whose
key already exists is skipped.

    uv run python apps/data_ops/db_restore_pks.py
"""
import pathlib

import duckdb

ROOT = bootstrap.ROOT
TARGETS = [
    ("option_bars", "(source, symbol, day)"),
    ("option_minutes", "(source, symbol, ts_utc)"),
    ("option_flow_daily", "(day, underlying, source)"),
    ("fundamentals_snapshot", "(day, symbol)"),
    ("underlying_daily", "(day, symbol, source)"),
    ("underlying_minutes", "(ts_utc, symbol, source)"),
    ("iv_history", "(day, underlying, source)"),
]


def has_key(con, table):
    rows = con.execute("""SELECT constraint_type FROM duckdb_constraints()
                          WHERE table_name=? AND constraint_type IN ('PRIMARY KEY','UNIQUE')""",
                       [table]).fetchall()
    return bool(rows)


def main():
    con = duckdb.connect(str(ROOT / "market_history.duckdb"))
    for table, key in TARGETS:
        try:
            exists = con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name=?",
                                 [table]).fetchone()[0]
            if not exists:
                print(f"{table}: absent — skipped"); continue
            if has_key(con, table):
                print(f"{table}: key present — skipped"); continue
            cols = con.execute(f"DESCRIBE {table}").fetchall()
            coldefs = ", ".join(f'"{c[0]}" {c[1]}' for c in cols)
            before = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            con.execute("BEGIN TRANSACTION")
            con.execute(f"CREATE TABLE {table}__new ({coldefs}, PRIMARY KEY {key})")
            con.execute(f"INSERT INTO {table}__new SELECT DISTINCT * FROM {table}")
            after = con.execute(f"SELECT count(*) FROM {table}__new").fetchone()[0]
            con.execute(f"DROP TABLE {table}")
            con.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
            con.execute("COMMIT")
            note = "" if after == before else f"  ({before - after} duplicate rows dropped)"
            print(f"{table}: key {key} restored, {after:,} rows{note}")
        except Exception as e:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            print(f"{table}: FAILED {repr(e)[:110]}")
    print("\nconstraints now:")
    for t, ct, cols in con.execute("""SELECT table_name, constraint_type, constraint_column_names
                                      FROM duckdb_constraints()
                                      WHERE constraint_type IN ('PRIMARY KEY','UNIQUE')
                                      ORDER BY table_name""").fetchall():
        print(f"  {t:22} {ct:12} {cols}")
    con.close()


if __name__ == "__main__":
    main()
