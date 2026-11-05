"""Snapshots it must NOT drop: a genuine second document, and a lone workbook.

Dropping one of these would destroy the only copy of something the user wrote, so the
bar for deleting is "every single row is already present, byte-identical, elsewhere".
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import journal_snapshots as J
from tests.extracted.apps.trade_log._journal_fixtures import NEW, OLD, FakeCon, rows_of

class TestItKeepsAGenuineSecondDocument(unittest.TestCase):
    def test_one_unshared_row_is_enough_to_keep_it(self):
        # If the smaller file has even one row the other lacks, it is a second document
        # and deleting it would destroy the only copy of something the user wrote.
        con = FakeCon(rows_of(OLD, 3) + rows_of(NEW, 5)
                      + [(OLD, 99, "only here")])
        self.assertEqual(J.drop_snapshots(con), [])

    def test_a_same_row_number_with_different_text_is_not_a_snapshot(self):
        # The key is (source_file, row_num) but the EVIDENCE is raw_json: the same row
        # edited in the newer file means the older one holds a version nobody else has.
        con = FakeCon(rows_of(OLD, 3) + rows_of(NEW, 5) + [(OLD, 2, "edited since")])
        self.assertEqual(J.drop_snapshots(con), [])

    def test_a_single_workbook_is_never_dropped(self):
        con = FakeCon(rows_of(NEW, 5))
        self.assertEqual(J.drop_snapshots(con), [])
