"""Which spreadsheet column is which schema column.

THE BUG. `journal_entries` had no `major_plan` column, and the loader mapped the first
eight cells POSITIONALLY onto the eight narrative columns. The sheet's fifth column is
"major plan", so from there on everything was shifted by one on every row ever loaded:
`alt_assumptions` held the plan, `confidence` held the alternative, and so on. No column
was empty and nothing failed -- each was full of the wrong text, which is why it lasted.
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import journal_fields as JF

# The real headers, verbatim -- long phrases, one of them with a parenthetical.
HEADER = ["date", "broker", "plan time", "major assumption", "major plan",
          "alternative assumptions（2-3)", "degree of confidence of major assumption",
          "plan fixed time", "order thinking", "orders/positions/avg"]


def row(**kw):
    out = [None] * len(HEADER)
    for name, v in kw.items():
        out[HEADER.index(name)] = v
    return out


class TestItMapsByName(unittest.TestCase):
    def test_the_plan_lands_in_major_plan(self):
        # THE regression. Its column did not exist, so the text went to alt_assumptions.
        vals = JF.values(HEADER, 405, row(**{"major plan": "roll to long DTE Dec 18"}),
                         {"major plan": "roll to long DTE Dec 18"}, "j.xlsx")
        got = dict(zip(JF.COLUMNS, vals))
        self.assertEqual(got["major_plan"], "roll to long DTE Dec 18")
        self.assertIsNone(got["alt_assumptions"], "the plan must not leak into its neighbour")

    def test_every_field_gets_its_own_text(self):
        cells = {"major assumption": "A", "major plan": "B",
                 "alternative assumptions（2-3)": "C",
                 "degree of confidence of major assumption": "D",
                 "plan fixed time": "E", "order thinking": "F"}
        got = dict(zip(JF.COLUMNS, JF.values(HEADER, 2, row(**cells), cells, "j.xlsx")))
        self.assertEqual(got["major_assumption"], "A")
        self.assertEqual(got["major_plan"], "B")
        self.assertEqual(got["alt_assumptions"], "C")
        self.assertEqual(got["confidence"], "D")
        self.assertEqual(got["plan_fixed_time"], "E")
        self.assertEqual(got["order_thinking"], "F")

    def test_major_assumption_and_major_plan_do_not_swap(self):
        # "major plan" starts with neither... but "major assumption" is a prefix of
        # nothing else, and a sloppy abbreviation to "major" would pair both with the
        # first match. Spelled out in full for exactly this reason.
        idx = JF.index_of(HEADER)
        self.assertNotEqual(idx["major_assumption"], idx["major_plan"])
        self.assertEqual(idx["major_assumption"], 3)
        self.assertEqual(idx["major_plan"], 4)


class TestAMissingHeaderIsNull(unittest.TestCase):
    def test_a_column_the_sheet_no_longer_has_is_none_not_a_neighbour(self):
        # Silently inheriting the next cell along is the entire failure mode here.
        short = HEADER[:4] + ["plan fixed time"]
        vals = JF.values(short, 2, row(**{"date": "2026-10-06"}), {"date": "2026-10-06"}, "j")
        got = dict(zip(JF.COLUMNS, vals))
        self.assertIsNone(got["major_plan"])
        self.assertIsNone(got["order_thinking"])
        self.assertEqual(got["plan_fixed_time"], None, "row has no such cell")

    def test_the_row_num_and_source_are_carried(self):
        got = dict(zip(JF.COLUMNS,
                       JF.values(HEADER, 405, row(**{"date": "x"}), {"date": "x"}, "j.xlsx")))
        self.assertEqual(got["row_num"], 405)
        self.assertEqual(got["source_file"], "j.xlsx")

    def test_raw_json_keeps_the_order_blocks_the_schema_has_no_column_for(self):
        # Columns 10+ hold the per-order block; raw_json is the only place they survive.
        cells = {"orders/positions/avg": "CRWD 220P"}
        got = dict(zip(JF.COLUMNS, JF.values(HEADER, 2, row(**cells), cells, "j")))
        self.assertEqual(json.loads(got["raw_json"])["orders/positions/avg"], "CRWD 220P")


if __name__ == "__main__":
    unittest.main()
