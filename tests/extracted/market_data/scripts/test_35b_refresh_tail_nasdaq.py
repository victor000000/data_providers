"""Tests for the Nasdaq price-tail refresher (market_data/scripts/35b_refresh_tail_nasdaq.py).
The OVERLAP TEST is the safety rule: a corporate action restates every earlier bar, so a
mismatch means a full refetch, never a patch. IDEMPOTENCY matters because a duplicated bar
feeds every IV series and screen in the repo.
"""
import importlib.util
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

import importlib
M = importlib.import_module("data_providers.warehouse.35b_refresh_tail_nasdaq")


def BARS(pairs):
    """{iso: (open, high, low, close, volume)} -- the shape datasources.nasdaq returns."""
    return {d: (10.0, 11.0, 9.0, float(c), 1000.0) for d, c in pairs}


class TestParsing(unittest.TestCase):
    def test_ddmmyyyy_not_mmddyyyy(self):
        # 23092026 is 23 Sep 2026; read as MMDDYYYY it is "month 23", which matched zero
        # overlapping days and so silently disabled the safety check below.
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "X.csv"
            p.write_text("23092026,1,2,0.5,1.5,10\n02012026,1,2,0.5,1.6,11\n")
            got, newest = M.read_last(p)
        self.assertEqual(newest, "2026-09-23")
        self.assertEqual(got["2026-09-23"], 1.5)

    def test_a_missing_file_reads_as_empty_not_a_crash(self):
        self.assertEqual(M.read_last(pathlib.Path("/nonexistent/x.csv")), ({}, None))


class TestOne(unittest.TestCase):
    """`one()` is the whole decision: verify against the overlap, then append or refuse."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "data" / "raw").mkdir(parents=True)
        (self.tmp / "data" / "adjusted").mkdir(parents=True)
        self._ws, M.WS = M.WS, self.tmp

    def tearDown(self):
        M.WS = self._ws

    def _csv(self, sym, lines):
        for sub in ("raw", "adjusted"):
            (self.tmp / "data" / sub / f"{sym}.csv").write_text("\n".join(lines) + "\n")

    def _read(self, sym="X"):
        return (self.tmp / "data" / "raw" / f"{sym}.csv").read_text()

    def test_appends_only_the_new_days(self):
        self._csv("X", ["22092026,1,2,0.5,100.0,10", "23092026,1,2,0.5,101.0,11"])
        M.daily_bars = lambda s: BARS([("2026-09-23", 101.0), ("2026-09-24", 102.0),
                                       ("2026-09-25", 103.0)])
        _, msg, n = M.one("X")
        self.assertEqual(n, 2, msg)
        self.assertEqual(len(self._read().splitlines()), 4)
        self.assertTrue(self._read().splitlines()[-1].startswith("25092026"))
        self.assertIn("25092026", (self.tmp / "data" / "adjusted" / "X.csv").read_text())

    def test_a_second_run_appends_nothing(self):
        self._csv("X", ["23092026,1,2,0.5,101.0,11"])
        M.daily_bars = lambda s: BARS([("2026-09-23", 101.0), ("2026-09-24", 102.0)])
        self.assertEqual(M.one("X")[2], 1)
        before = self._read()
        self.assertEqual(M.one("X")[2], 0, "re-running must not duplicate")
        self.assertEqual(self._read(), before)

    def test_overlap_difference_skips_the_symbol(self):
        self._csv("X", ["23092026,1,2,0.5,101.0,11"])
        M.daily_bars = lambda s: BARS([("2026-09-23", 999.0), ("2026-09-24", 102.0)])
        _, msg, n = M.one("X")
        self.assertEqual(n, 0)
        self.assertIn("OVERLAP DIFFERS", msg)
        self.assertEqual(len(self._read().splitlines()), 1, "CSV must be left untouched")

    def test_unverifiable_input_is_reported_not_appended(self):
        # No shared day or no bars at all: nothing to verify against, so appending is a guess.
        # UNKNOWN rather than FAILED -- from here a delisted symbol and a flaky API are the
        # same thing, and only an independent source can say which (see the module docstring).
        self._csv("X", ["23092026,1,2,0.5,101.0,11"])
        M.daily_bars = lambda s: BARS([("2030-12-01", 5.0)])
        self.assertIn("UNKNOWN", M.one("X")[1])
        self.assertEqual(len(self._read().splitlines()), 1, "CSV must be left untouched")
        M.daily_bars = lambda s: {}
        self.assertIn("UNKNOWN", M.one("X")[1])
        self.assertIn("FAILED", M.one("NOSUCHFILE")[1], "a missing CSV IS a real failure")


if __name__ == "__main__":
    unittest.main()
