"""journal_snapshots.py — one journal, several snapshots of it.

The user's workbook is a living document they re-save, so `../trading_logs` accumulates
captures of the SAME journal rather than a new one per file. Measured 2026-10-06:
`2026.xlsx` (371 rows) and `2026_tradelogs.xlsx` (392) share 371 row numbers and every
one of those rows is byte-identical -- the older file is a strict prefix of the newer.

Loading both is not harmless. `journal_entries` keys on (source_file, row_num), so every
historical day is stored TWICE under two names, and any count of journal entries -- or
any query that sums or lists them -- silently doubles. The rows are not wrong
individually; the set is.

The rule: if EVERY row of a workbook is already present, identical, in another workbook
with more rows, that workbook is a snapshot and is dropped. A workbook with even one row
the others lack is kept -- it is a genuine second document, and deciding otherwise would
throw away the only copy of something the user wrote.
"""


def workbooks(d=None):
    """Every journal workbook in a directory, newest naming aside, hand-saved first.

    `~$*.xlsx` is Excel's own lock file -- a few hundred bytes of the owner's username,
    and openpyxl raises on it.
    """
    import pathlib

    from data_providers.jobs.trades import source_dirs
    found = (sorted(pathlib.Path(d).glob("*.xlsx")) if d
             else [pathlib.Path(f) for f in source_dirs.find("*.xlsx")])
    return [f for f in found if not f.name.startswith("~$")]


def _all_rows_duplicated(con, src, total):
    dup = con.execute("""
        SELECT count(*) FROM journal_entries s
         WHERE s.source_file = ?
           AND EXISTS (SELECT 1 FROM journal_entries b
                        WHERE b.source_file <> s.source_file
                          AND b.row_num = s.row_num
                          AND b.raw_json = s.raw_json)""", [src]).fetchone()[0]
    return dup == total


def drop_snapshots(con):
    """Delete every workbook that is a content-subset of a bigger one. Returns the names."""
    counts = dict(con.execute(
        "SELECT source_file, count(*) FROM journal_entries GROUP BY 1").fetchall())
    dropped = []
    for src, total in counts.items():
        if not any(n > total for o, n in counts.items() if o != src):
            continue                      # nothing bigger to be a snapshot OF
        if _all_rows_duplicated(con, src, total):
            con.execute("DELETE FROM journal_entries WHERE source_file = ?", [src])
            dropped.append(src)
    return dropped
