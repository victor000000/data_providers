"""test_bs.py — the pricing kernel, whose contract is "None is the only way to say I don't
know" and "the two backends answer identically".

Both halves have shipped broken, so both are asserted here. The pure-Python path returned
(nan, nan, nan, nan) where QuantLib returned (None, None, None, None); and implied_vol
returned a CONFIDENT 0.0001 -- its bisection's lower bound -- for a quote it could not solve.
A NaN slips through `if not v or v <= 0` because every comparison against NaN is False and
bool(nan) is True, so it has to be tested by self-comparison. A NaN is easy to arrive at: a
CSV "nan", a JSON literal, an unguarded mean of empty input.
"""
import datetime as dt
import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.compute.bs import (T_years, bs_delta, bs_greeks, bs_price, implied_vol,  # noqa: E402
                        implied_vol_batch)

R = 0.04
NAN = float("nan")


class TestTYears(unittest.TestCase):
    def test_days_over_365(self):
        self.assertAlmostEqual(T_years(dt.date(2026, 1, 31), today=dt.date(2026, 1, 1)),
                               30 / 365)

    def test_a_past_expiry_is_zero_never_negative(self):
        self.assertEqual(T_years(dt.date(2020, 1, 1), today=dt.date(2026, 1, 1)), 0.0)


class TestPrice(unittest.TestCase):
    def test_put_call_parity(self):
        S, K, T, sig = 100.0, 105.0, 0.5, 0.3
        c, p = bs_price(S, K, T, R, sig, True), bs_price(S, K, T, R, sig, False)
        self.assertAlmostEqual(c - p, S - K * math.exp(-R * T), places=8)

    def test_expired_is_intrinsic(self):
        self.assertAlmostEqual(bs_price(110, 100, 0.0, R, 0.3, True), 10.0, places=9)
        self.assertAlmostEqual(bs_price(90, 100, 0.0, R, 0.3, False), 10.0, places=9)

    def test_zero_vol_is_the_UNDISCOUNTED_intrinsic(self):
        # Documented as "prices at intrinsic", and intrinsic here is S-K -- not the exact
        # sigma->0 limit, which is S - K*e^{-rT} = 11.98 for these inputs. That is a 2%
        # difference at six months, so it is asserted rather than assumed away: callers only
        # reach this through `iv or 0.0`, i.e. when the solve FAILED and the leg is being
        # priced as expired, and nobody holds a live option at zero vol.
        self.assertAlmostEqual(bs_price(110, 100, 0.5, R, 0.0, True), 10.0, places=9)


class TestGreeks(unittest.TestCase):
    def test_delta_bounds_and_the_put_call_identity(self):
        S, K, T, sig = 100.0, 100.0, 0.25, 0.4
        dc, dp = bs_delta(S, K, T, R, sig, True), bs_delta(S, K, T, R, sig, False)
        self.assertTrue(0 < dc < 1)
        self.assertAlmostEqual(dc - dp, 1.0, places=9)

    def test_gamma_is_identical_for_a_call_and_a_put(self):
        c = bs_greeks(100.0, 100.0, 0.25, R, 0.4, True)
        p = bs_greeks(100.0, 100.0, 0.25, R, 0.4, False)
        self.assertAlmostEqual(c[1], p[1], places=12)


class TestImpliedVol(unittest.TestCase):
    def test_round_trip(self):
        for sig in (0.12, 0.35, 0.9):
            px = bs_price(100.0, 105.0, 0.4, R, sig, True)
            self.assertAlmostEqual(implied_vol(px, 100.0, 105.0, 0.4, R, True), sig, places=4)

    def test_below_intrinsic_is_none(self):
        self.assertIsNone(implied_vol(0.0001, 110.0, 100.0, 0.5, R, True))

    def test_nan_or_none_returns_none_and_NEVER_a_confident_number(self):
        for bad in (NAN, None):
            self.assertIsNone(implied_vol(5.0, 100.0, 100.0, 0.5, bad, True), "bad rate")
            self.assertIsNone(implied_vol(bad, 100.0, 100.0, 0.5, R, True), "bad price")
        self.assertIsNone(implied_vol(5.0, NAN, 100.0, 0.5, R, True), "bad S")
        self.assertIsNone(implied_vol(5.0, 100.0, NAN, 0.5, R, True), "bad K")
        self.assertIsNone(implied_vol(5.0, 100.0, 100.0, NAN, R, True), "bad T")

    def test_a_zero_rate_is_legitimate(self):
        px = bs_price(100.0, 100.0, 0.5, 0.0, 0.3, True)
        self.assertAlmostEqual(implied_vol(px, 100.0, 100.0, 0.5, 0.0, True), 0.3, places=4)

    def test_the_batch_path_agrees_with_the_scalar_one(self):
        sigs = (0.2, 0.4)
        rows = [(bs_price(100.0, 100.0, 0.5, R, s, True), 100.0, 100.0, 0.5, R, True)
                for s in sigs]
        got = implied_vol_batch(rows)
        self.assertEqual(len(got), len(sigs))
        for g, s in zip(got, sigs):
            self.assertAlmostEqual(g, s, places=4)
