import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

# --help exits before any database write.
from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap below.

"""load_journal.py — the user's raw trading journal -> `journal_entries`.

One row per spreadsheet row: the named narrative columns plus raw_json. Idempotent
per source file -- key (source_file, row_num).

READS THE SOURCE DIRECTORY, not the repo's copy. It used to open `journal/2026.xlsx`
verbatim, and the hand-maintained workbook ran five sessions ahead: 2026-10-01
through 10-06 sat unread while the database stopped at 09-30.

Usage: uv run python load_journal.py [--dir PATH]
"""
import argparse
import json
import pathlib

import duckdb
import openpyxl

from data_providers.jobs.trades import journal_fields as jf
from data_providers.jobs.trades import journal_snapshots as snapshots
from data_providers.jobs.trades.journal_snapshots import workbooks

ROOT = bootstrap.ROOT
DB = ROOT / "trades.duckdb"


def load(SRC, con):
    wb = openpyxl.load_workbook(SRC, read_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise ValueError("Empty workbook; existing journal retained")
    header = [str(c).strip() if c is not None else f"col_{j}" for j, c in enumerate(rows[0])]

    prepared = []
    for i, r in enumerate(rows[1:], start=2):  # skip the header row
        cells = {header[j]: str(c).strip() for j, c in enumerate(r)
                 if c is not None and str(c).strip()}
        if not cells:
            continue
        prepared.append(jf.values(header, i, r, cells, SRC.name))
    if not prepared:
        raise ValueError("No journal entries; existing journal retained")
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM journal_entries WHERE source_file=?", [SRC.name])
        for vals in prepared:
            con.execute(f"INSERT INTO journal_entries ({','.join(jf.COLUMNS)}) "
                    f"VALUES ({','.join('?' * len(jf.COLUMNS))})", vals)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print(f"  {SRC.name}: {len(prepared)} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None)
    a = ap.parse_args()
    files = workbooks(a.dir)
    if not files:
        print("no journal workbook found"); return 1
    con = duckdb.connect(str(DB))
    for f in files:
        load(f, con)
    for gone in snapshots.drop_snapshots(con):
        print(f"  {gone}: dropped — a snapshot of a larger workbook, every row identical")
    # The newest note the user wrote, so a stale load is visible. `ts` holds BOTH shapes --
    # '20260925' and '2026-09-28 00:00:00' -- so ORDER BY ts DESC is string ordering and
    # put 09-25 above 10-06 ('0' > '-'). Normalise the compact form.
    last = con.execute("""SELECT stamp, source_file, substr(major_assumption,1,60)
        FROM (SELECT *,coalesce(ts,CASE WHEN regexp_matches(plan_time,
             '^[0-9]{4}-[0-9]{2}-[0-9]{2} ') THEN plan_time END) stamp FROM journal_entries)
        WHERE stamp IS NOT NULL ORDER BY CASE WHEN regexp_matches(stamp, '^[0-9]{8}$')
                      THEN substr(stamp,1,4)||'-'||substr(stamp,5,2)||'-'||substr(stamp,7,2)
                      ELSE stamp END DESC LIMIT 1""").fetchone() or (None,None,None)
    kept, nsrc = con.execute(
        "SELECT count(*), count(DISTINCT source_file) FROM journal_entries").fetchone()
    print(f"journal_entries: {kept} rows over {nsrc} workbook(s); "
          f"newest note {last[0]} ({last[1]}) {last[2] or ''}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
