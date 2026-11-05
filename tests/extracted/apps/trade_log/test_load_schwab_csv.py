"""`money()` -- the number parser every Schwab loader is built on.

THE BUG. Schwab writes sub-dollar option prices without a leading zero: `.27`, `.85`,
`.6662`. The pattern was `[-+]?[\\d,]+\\.?\\d*`, which needs a digit BEFORE the point, so
on `.27` it began matching at the `2` and returned 27.0. Every such price was read 100x
too high -- a TLT call at 0.27 became 27.00, an EWZ call at 0.6662 became 6,662. Nothing
failed; the number was simply wrong, and it flows into fills, P&L and every target.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades.load_schwab_csv import money


class TestTheLeadingDot(unittest.TestCase):
    def test_a_bare_dot_decimal_is_not_multiplied_by_100(self):
        self.assertEqual(money(".27"), 0.27)
        self.assertEqual(money(".6662"), 0.6662)
        self.assertEqual(money(".5"), 0.5)

    def test_it_still_reads_a_normal_decimal(self):
        self.assertEqual(money("2.71"), 2.71)
        self.assertEqual(money("27"), 27.0)

    def test_a_whole_number_stays_whole(self):
        self.assertEqual(money("110"), 110.0)


class TestTheOtherForms(unittest.TestCase):
    def test_parentheses_mean_negative(self):
        # The account's buying power prints as `($233.33)`. Returned positive it reads
        # as headroom the account does not have.
        self.assertEqual(money("($233.33)"), -233.33)
        self.assertEqual(money("(1,020.00)"), -1020.0)

    def test_a_leading_minus_is_negative(self):
        self.assertEqual(money("-0.02"), -0.02)

    def test_dollar_signs_and_commas_come_off(self):
        self.assertEqual(money("$1,858.00"), 1858.0)

    def test_an_absent_value_is_none_not_zero(self):
        # None and 0.0 mean different things downstream: 0.0 is a price, None is no
        # reading at all, and callers use `or` on it.
        for blank in (None, "", "-"):
            self.assertIsNone(money(blank), f"{blank!r} should be None")

    def test_garbage_does_not_raise(self):
        self.assertIsNone(money("n/a"))


if __name__ == "__main__":
    unittest.main()
