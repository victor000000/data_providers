"""Measuring a statement's export clock against fills the log already holds.

Zero is not a neutral default: the Account Statement prints no generation time, so the
header-based recovery returned 0 and an eight-hour CST export would be filed eight
hours late, on the wrong side of midnight UTC.
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import schwab_clock as C


class FakeCon:
    """Returns a canned UTC timestamp for the six-field match."""

    def __init__(self, utc=None, boom=False):
        self.utc, self.boom = utc, boom

    def execute(self, sql, params):
        if self.boom:
            raise RuntimeError("no such table")
        return self

    def fetchone(self):
        return (self.utc,)


def fill(ts_naive, price=0.27):
    return {"underlying": "TLT", "strike": 82.0, "call_put": "C", "action": "BUY",
            "qty": 2, "price": price, "ts_utc": ts_naive}


NAIVE = dt.datetime(2026, 10, 6, 21, 50, 47)


class TestTheSign(unittest.TestCase):
    def test_a_cst_export_yields_plus_eight(self):
        # `utc = naive - offset`, so the offset is (naive - utc). Returning the reverse
        # gave UTC-8 for a CST export and moved every fill eight hours the WRONG way --
        # it filed a 10/5 13:44 fill under 10/6.
        con = FakeCon(utc=NAIVE - dt.timedelta(hours=8))
        offset, _ = C.offset_from_log(con, [fill(NAIVE)])
        self.assertEqual(offset, dt.timedelta(hours=8))

    def test_a_utc_export_yields_zero(self):
        con = FakeCon(utc=NAIVE)
        offset, _ = C.offset_from_log(con, [fill(NAIVE)])
        self.assertEqual(offset, dt.timedelta(0))


class TestTheWindow(unittest.TestCase):
    def test_eight_hours_is_inside_the_window(self):
        # A 90-minute tolerance rejected the very signal being measured and matched
        # nothing at all -- the thing being measured IS the offset.
        con = FakeCon(utc=NAIVE - dt.timedelta(hours=8))
        self.assertIsNotNone(C.offset_from_log(con, [fill(NAIVE)])[0])

    def test_beyond_a_real_timezone_is_rejected(self):
        con = FakeCon(utc=NAIVE - dt.timedelta(hours=20))
        self.assertIsNone(C.offset_from_log(con, [fill(NAIVE)])[0])


class TestNoEvidence(unittest.TestCase):
    def test_no_match_returns_none_so_the_caller_can_refuse(self):
        self.assertEqual(C.offset_from_log(FakeCon(utc=None), [fill(NAIVE)]), (None, None))

    def test_no_fills_returns_none(self):
        self.assertEqual(C.offset_from_log(FakeCon(utc=NAIVE), []), (None, None))

    def test_a_broken_query_returns_none_rather_than_raising(self):
        self.assertEqual(C.offset_from_log(FakeCon(boom=True), [fill(NAIVE)]), (None, None))


class TestDisagreement(unittest.TestCase):
    def test_two_equally_supported_clocks_resolve_to_nothing(self):
        # Tied evidence means the statement is not self-consistent and no offset can be
        # written. Guessing one would be worse than refusing.
        class Split(FakeCon):
            def __init__(self):
                super().__init__()
                self.n = 0

            def fetchone(self):
                self.n += 1
                return (NAIVE - dt.timedelta(hours=8 if self.n == 1 else 5),)

        self.assertIsNone(C.offset_from_log(Split(), [fill(NAIVE), fill(NAIVE)])[0])


if __name__ == "__main__":
    unittest.main()
