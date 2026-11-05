"""The journal loader, against a literal workbook and a column-aware fake.

The regression is scope, not logic: this opened `journal/2026.xlsx` in the repo while
the user's hand-maintained workbook ran five sessions ahead, so 2026-10-01 through
10-06 -- plan, alternative, confidence, post-session review -- were never recorded and
nothing failed.
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

import openpyxl

from data_providers.jobs.trades import load_journal as L

HEADER = ["date", "broker", "plan time", "major assumption", "major plan",
          "alternative assumptions", "degree of confidence", "plan fixed time",
          "order thinking", "orders/positions/avg"]
ENTRY = ["2026-10-06 00:00:00", "ibkr/schwab", "18:42:00", None,
         "care about the CRWD put legs", "lack of buying power", "0.7", "19:51:00",
         "note", "CRWD 220P"]


def workbook(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    wb.save(path)


class FakeCon:
    """Stores rows BY COLUMN NAME, so a named INSERT and a positional one both work."""

    def __init__(self):
        self.rows, self.deleted = [], []

    def execute(self, sql, params=None):
        if sql.startswith("DELETE"):
            self.deleted.append(params[0])
            self.rows = [r for r in self.rows if r["source_file"] != params[0]]
        elif sql.startswith("INSERT"):
            cols = sql.split("(", 1)[1].split(")", 1)[0].split(",")
            self.rows.append(dict(zip([c.strip() for c in cols], params)))
        return self


class TestLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "2026_tradelogs.xlsx"
        workbook(self.path, [ENTRY, [None] * len(HEADER)])
        self.con = FakeCon()

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_stores_every_row_that_has_content(self):
        L.load(self.path, self.con)
        self.assertEqual(len(self.con.rows), 1, "a blank row is not an entry")

    def test_the_plan_lands_in_major_plan_not_in_its_neighbour(self):
        # The column did not exist and a positional map put this text in alt_assumptions.
        L.load(self.path, self.con)
        row = self.con.rows[0]
        self.assertEqual(row["major_plan"], "care about the CRWD put legs")
        self.assertIsNone(row["major_assumption"])
        self.assertEqual(row["alt_assumptions"], "lack of buying power")
        self.assertEqual(row["confidence"], "0.7")
        self.assertEqual(row["plan_fixed_time"], "19:51:00")

    def test_the_row_num_counts_the_header_as_row_one(self):
        L.load(self.path, self.con)
        self.assertEqual(self.con.rows[0]["row_num"], 2)

    def test_a_reload_replaces_rather_than_appends(self):
        # Idempotent per source file. Without the DELETE first, every re-run doubles the
        # journal -- which is exactly the failure the two-workbook case produced.
        L.load(self.path, self.con)
        L.load(self.path, self.con)
        self.assertEqual(len(self.con.rows), 1)
        self.assertEqual(self.con.deleted, [self.path.name, self.path.name])

    def test_raw_json_keeps_the_cells_the_named_columns_drop(self):
        # The order blocks live in columns the schema has no column for; raw_json is the
        # only place they survive, and they are the record of what was traded.
        L.load(self.path, self.con)
        raw = json.loads(self.con.rows[0]["raw_json"])
        self.assertEqual(raw["orders/positions/avg"], "CRWD 220P")
        self.assertEqual(raw["order thinking"], "note")


if __name__ == "__main__":
    unittest.main()
