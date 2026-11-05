"""Tests for shared/rates.py — the constants 21 files used to re-declare."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.shared import rates


class TestValues(unittest.TestCase):
    """Tier 1: pure, no database, no network."""

    def test_us_rate_is_flat_four_percent(self):
        # Flat on purpose: the horizon does not justify a term structure, and that
        # decision is recorded once here rather than in a comment in twelve files.
        self.assertEqual(rates.R_US, 0.04)

    def test_cn_rate_is_distinct_from_us(self):
        # These are two different currencies' rates. Collapsing them to one value
        # would price the CZCE legs off the US curve.
        self.assertNotEqual(rates.R_CN, rates.R_US)

    def test_fx_is_usdcny(self):
        self.assertEqual(rates.FX_CNY, 7.13)


class TestShocks(unittest.TestCase):
    def test_ladder_is_the_seven_point_symmetric_one(self):
        self.assertEqual(rates.SHOCKS,
                         [-0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15])

    def test_ladder_is_symmetric_and_contains_zero(self):
        # The risk profile prints this ladder as a row; a ladder that did not
        # contain 0 would make the "no move" column disappear silently.
        self.assertIn(0.0, rates.SHOCKS)
        self.assertEqual(sorted(rates.SHOCKS),
                         sorted(-s for s in rates.SHOCKS))

    def test_ladder_and_curve_grid_are_different_things(self):
        # Both were called SHOCKS. One is the seven-point comparison ladder, the
        # other is margin_curve's 1%-step sweep for finding a trough.
        self.assertEqual(len(rates.SHOCKS), 7)
        self.assertEqual(len(rates.CURVE_GRID), 41)
        self.assertEqual(rates.CURVE_GRID[0], -0.20)
        self.assertEqual(rates.CURVE_GRID[-1], 0.20)


class TestCurrencyHelpers(unittest.TestCase):
    def test_cn_broker_is_converted(self):
        self.assertAlmostEqual(rates.to_usd(713.0, "浙商期货"), 100.0)

    def test_us_broker_passes_through(self):
        self.assertEqual(rates.to_usd(713.0, "IBKR"), 713.0)
        self.assertEqual(rates.to_usd(713.0, "Schwab"), 713.0)

    def test_fx_of_matches_to_usd(self):
        # Two spellings of one rule; they must not drift apart.
        for broker in ("IBKR", "Schwab", "浙商期货"):
            with self.subTest(broker=broker):
                self.assertAlmostEqual(
                    rates.to_usd(100.0, broker), 100.0 / rates.fx_of(broker))

    def test_a_renamed_cn_broker_would_convert_wrongly(self):
        # Guards the assumption the helper encodes: anything not exactly the CNY
        # broker is treated as USD. If the broker string ever changes, every
        # aggregate silently stops converting, so pin the behaviour here.
        self.assertEqual(rates.fx_of("浙商"), 1.0)


if __name__ == "__main__":
    unittest.main()
