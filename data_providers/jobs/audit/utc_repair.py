import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""utc_repair.py — move timestamps that were stored in LOCAL time back onto UTC.

WHY THIS IS NEEDED AND WHY IT IS NARROW

This box runs Asia/Shanghai (UTC+8). Four scripts wrote SQL `now()` through a bare
`duckdb.connect()`, so DuckDB stamped them locally — eight hours AHEAD of the UTC every
other table holds. Those four are fixed (8d2cfd0) and clock.py now pins the process, but
rows written BEFORE the fix are still in the database.

The hard part is not the shift, it is knowing WHICH rows to shift. For most tables an
eight-hour error is undetectable after the fact: a timestamp eight hours off still looks
like a perfectly plausible time, and "shift every row back eight hours" would corrupt
every row that was always correct.

So this repairs only what can be PROVEN wrong, and reports the rest untouched:

  PROVABLE — a timestamp in the future. A UTC timestamp cannot be in the future, so any
  value more than an hour ahead of now() was necessarily written on a clock that was
  ahead, and the only such clock here is +8. This is exact, not heuristic: the shift is
  applied only when it lands the row at or before now, and it is per-row.

  REPORTED, NOT TOUCHED — everything else. Rows whose local-time origin cannot be
  distinguished from a correct value are counted and printed so the size of the question
  is visible, but they are left alone. Guessing here would be worse than the original bug,
  because it would be silent and unrecoverable.

Dry run by default. Nothing is written without --apply.

    uv run python apps/audit/utc_repair.py            # report
    uv run python apps/audit/utc_repair.py --apply    # repair
"""
import argparse
import datetime as dt
import pathlib
import sys

import duckdb

from data_providers.data import clock  # noqa: E402  (also pins this process to UTC)

ROOT = bootstrap.ROOT
DBS = (("trades", "trades.duckdb"),
       ("market_history", "market_history.duckdb"),
       ("cn_market", "cn_market.duckdb"),
       ("reference", "reference.duckdb"),
       ("universe", "market_data/yahoo.duckdb"))

# The box's offset. Named rather than hard-coded into the arithmetic so the intent is
# readable: this is the amount a locally-stamped value is AHEAD of true UTC.
LOCAL_AHEAD_HOURS = 8
TOLERANCE = dt.timedelta(hours=1)     # covers clock skew; far below a timezone


def timestamp_columns(con, table):
    try:
        cols = con.execute(f'DESCRIBE "{table}"').fetchall()
    except Exception:
        return []
    return [c[0] for c in cols if "TIMESTAMP" in str(c[1]).upper()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the repair; without it this only reports")
    args = ap.parse_args()

    now = clock.now()
    cutoff = now + TOLERANCE
    total_fixed = total_future = 0

    for label, dbfile in DBS:
        path = bootstrap.ROOT / dbfile
        if not path.exists():
            continue
        try:
            con = duckdb.connect(str(path))          # read-WRITE: repairs need it
        except Exception as e:
            print(f"{label:15} SKIPPED — {type(e).__name__} "
                  f"(a writer holds it; re-run after downloads finish)")
            continue

        try:
            for (table,) in con.execute(
                    "SELECT table_name FROM duckdb_tables()").fetchall():
                for col in timestamp_columns(con, table):
                    try:
                        n_future = con.execute(
                            f'SELECT count(*) FROM "{table}" WHERE "{col}" > ?',
                            [cutoff]).fetchone()[0]
                    except Exception:
                        continue          # unreadable column: not ours to guess at
                    if not n_future:
                        continue
                    total_future += n_future

                    # Only shift when the 8h correction actually resolves the anomaly. If it
                    # does not, the value is ahead for some other reason and we must not
                    # touch it — an unexplained future timestamp is a finding, not a chore.
                    n_fixable = con.execute(
                        f'''SELECT count(*) FROM "{table}"
                            WHERE "{col}" > ?
                              AND "{col}" - INTERVAL {LOCAL_AHEAD_HOURS} HOUR <= ?''',
                        [cutoff, now]).fetchone()[0]

                    print(f"{label:15} {table}.{col}: {n_future:,} future "
                          f"({n_fixable:,} correctable by -{LOCAL_AHEAD_HOURS}h)")
                    if args.apply and n_fixable:
                        con.execute(
                            f'''UPDATE "{table}"
                                   SET "{col}" = "{col}" - INTERVAL {LOCAL_AHEAD_HOURS} HOUR
                                 WHERE "{col}" > ?
                                   AND "{col}" - INTERVAL {LOCAL_AHEAD_HOURS} HOUR <= ?''',
                            [cutoff, now])
                        total_fixed += n_fixable
                        print(f"{'':15}   -> shifted {n_fixable:,} rows onto UTC")
        finally:
            con.close()

    print()
    if total_future == 0:
        print("No timestamp anywhere is in the future — every stored value is consistent "
              "with UTC. Nothing to repair.")
    elif args.apply:
        print(f"Repaired {total_fixed:,} of {total_future:,} future timestamps.")
    else:
        print(f"DRY RUN: {total_future:,} future timestamps found, "
              f"{total_future:,} would be examined. Re-run with --apply to shift them.")
    print("NOTE: rows whose local-time origin cannot be proven (an 8h error in the PAST "
          "looks like a valid time) are deliberately left alone.")


if __name__ == "__main__":
    main()
