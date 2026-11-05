"""Choosing between a printed clock and an inferred one.

A clock PRINTED in the report beats one INFERRED from a shared fill -- it is the
statement talking about itself. This flavour prints none, so in practice the anchor
wins; the ordering is asserted anyway because a fallback that silently outranks the
evidence is how the two drift apart.
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import schwab_clock_batch as CB


class TestPick(unittest.TestCase):
    def test_a_printed_header_clock_beats_an_inferred_one(self):
        got = CB.pick((dt.timedelta(hours=8), None, "measured"), dt.timedelta(hours=5))
        self.assertEqual(got, (dt.timedelta(hours=5), "from the report header"))

    def test_with_no_header_the_measured_offset_stands(self):
        off, how = CB.pick((dt.timedelta(hours=8), ("SWKS", 110.0, "C", "BUY"), "measured"),
                           dt.timedelta(0))
        self.assertEqual(off, dt.timedelta(hours=8))
        self.assertIn("SWKS", how)

    def test_no_offset_stays_none(self):
        self.assertIsNone(CB.pick((None, None, "measured"), dt.timedelta(0))[0])


if __name__ == "__main__":
    unittest.main()
