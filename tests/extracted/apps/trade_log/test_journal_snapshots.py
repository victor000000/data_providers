"""One journal, several saved snapshots of it -- the case for dropping one.

The user re-saves the same workbook, so `../trading_logs` accumulates captures rather
than documents. Measured 2026-10-06: `2026.xlsx` (371 rows) and `2026_tradelogs.xlsx`
(392) share 371 row numbers and every one is byte-identical. `journal_entries` keys on
(source_file, row_num), so loading both stores all of history twice and doubles any
count of it -- the rows are each right, the SET is not.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import journal_snapshots as J
from tests.extracted.apps.trade_log._journal_fixtures import NEW, OLD, FakeCon, rows_of


class TestItDropsASnapshot(unittest.TestCase):
    def test_a_strict_prefix_is_dropped(self):
        con = FakeCon(rows_of(OLD, 3) + rows_of(NEW, 5))
        self.assertEqual(J.drop_snapshots(con), [OLD])

    def test_the_bigger_workbook_is_kept(self):
        con = FakeCon(rows_of(OLD, 3) + rows_of(NEW, 5))
        J.drop_snapshots(con)
        self.assertEqual({r[0] for r in con.rows}, {NEW})

    def test_equal_sized_workbooks_are_both_kept(self):
        # Neither is a snapshot OF the other, so there is no basis to delete either.
        con = FakeCon(rows_of(OLD, 3) + rows_of(NEW, 3))
        self.assertEqual(J.drop_snapshots(con), [])


class TestWorkbookDiscovery(unittest.TestCase):
    def test_the_excel_lock_file_is_not_a_journal(self):
        # `~$2026.xlsx` is a few hundred bytes of the owner's username and openpyxl
        # raises on it. It sits in the source directory right now.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "2026.xlsx").write_bytes(b"x")
            (pathlib.Path(d) / "~$2026.xlsx").write_bytes(b"x")
            got = [f.name for f in J.workbooks(d)]
        self.assertEqual(got, ["2026.xlsx"])


if __name__ == "__main__":
    unittest.main()
