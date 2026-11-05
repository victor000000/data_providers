"""Tests for option_universe — which contracts the ATM sweep asks for.

Split out of download_atm_iv.py (see that module's docstring for why). It had no tests at all
inline, and every function here carries a silent-failure note that only a test can hold still:
a wrong OCC root returns HTTP 400, which the batch loop records as ZERO BARS — indistinguishable
from a contract that never traded — and a strike ladder that is too coarse simply returns a
narrower smile with no error at all.
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.history import option_universe as U  # noqa: E402


class TestOcc(unittest.TestCase):
    def test_builds_the_occ_form(self):
        # root + YYMMDD + C/P + strike in thousandths, zero-padded to 8.
        self.assertEqual(U.occ("AAPL", dt.date(2026, 11, 20), "C", 150),
                         "AAPL261120C00150000")

    def test_strike_is_thousandths_not_dollars(self):
        self.assertEqual(U.occ("AAPL", dt.date(2026, 11, 20), "P", 7.5),
                         "AAPL261120P00007500")

    def test_a_class_share_loses_its_punctuation(self):
        # THE silent failure: Yahoo spells it BRK-B, the vendor wants BRKB. Passing the Yahoo
        # form through made every class-share batch fail with HTTP 400, which the batch loop
        # records as zero bars -- a wrong answer that looks like an empty contract.
        self.assertTrue(U.occ("BRK-B", dt.date(2026, 11, 20), "C", 500).startswith("BRKB"))
        self.assertTrue(U.occ("CRD-A", dt.date(2026, 11, 20), "C", 100).startswith("CRDA"))

    def test_an_unmapped_name_falls_back_rather_than_raising(self):
        # canonical_for returns None for a name missing from the map; a download must not stop.
        sym = U.occ("ZZZZ-NOTREAL", dt.date(2026, 11, 20), "P", 10)
        self.assertTrue(sym.startswith("ZZZZNOTREAL"))


class TestIncrements(unittest.TestCase):
    def test_every_level_returns_a_ladder(self):
        for s in (5, 30, 100, 575, 2000):
            with self.subTest(S=s):
                self.assertTrue(U.increments(s), f"no increments at ${s}")

    def test_the_ladder_stays_near_the_price(self):
        # A coarse ladder alone silently loses the +/-2 strikes: META at $575 was offered only
        # 10s and 25s, so 565/585 were never requested and the smile came back 2 wide.
        for s in (5, 30, 100, 575, 2000):
            with self.subTest(S=s):
                self.assertLessEqual(min(U.increments(s)), max(2.5, s * 0.02))


class TestCandidates(unittest.TestCase):
    def test_covers_both_sides_of_the_money(self):
        ks = U.candidates(100)
        self.assertLess(min(ks), 100)
        self.assertGreater(max(ks), 100)

    def test_never_returns_a_non_positive_strike(self):
        self.assertTrue(all(k > 0 for k in U.candidates(3)))

    def test_reach_must_exceed_the_band_it_serves(self):
        # reach=6 covers the +/-5 default at every increment; a smaller reach would silently
        # truncate the outer strikes with no error.
        ks = U.candidates(575, reach=6)
        self.assertGreaterEqual(len(ks), 11)

    def test_sorted_and_unique(self):
        ks = U.candidates(100)
        self.assertEqual(ks, sorted(set(ks)))


class TestExpiries(unittest.TestCase):
    def test_third_friday_is_a_friday(self):
        self.assertEqual(U.third_friday(2026, 9).weekday(), 4)
        self.assertEqual(U.third_friday(2026, 9), dt.date(2026, 9, 18))

    def test_monthly_expiries_are_ordered_and_bounded(self):
        out = U.monthly_expiries(dt.date(2026, 9, 1), dt.date(2026, 12, 31))
        self.assertEqual(out, sorted(out))
        self.assertEqual(out[0], dt.date(2026, 9, 18))
        self.assertTrue(all(dt.date(2026, 9, 1) <= e <= dt.date(2026, 12, 31) for e in out))

    def test_a_window_with_no_expiry_is_empty(self):
        self.assertEqual(U.monthly_expiries(dt.date(2026, 9, 19), dt.date(2026, 10, 15)), [])


if __name__ == "__main__":
    unittest.main()
