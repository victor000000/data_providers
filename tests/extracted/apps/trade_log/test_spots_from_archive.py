"""The fill-time spot from `underlying_minutes`.

yfinance's 5-minute bars reach five days and no further, so every older fill silently
kept a NULL `underlying_px` -- ten journal option fills from 2026-09-29 among them,
with a strike, a price and an expiry and no underlying price to solve an IV against.
The archive holds 1-minute bars from 2024-01 and is already on disk.
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import spots_from_archive as A

TS = dt.datetime(2026, 9, 29, 14, 0, 0)


class FakeCon:
    """Answers the nearest-prior query, and records what it was asked for."""

    def __init__(self, close=None):
        self.close, self.asked = close, []

    def execute(self, sql, params=None):
        self.asked.append(params)
        return self

    def fetchone(self):
        return (self.close,) if self.close is not None else None


class TestSpotAt(unittest.TestCase):
    def test_it_returns_the_bar_close_and_names_its_source(self):
        spot, src = A.spot_at(FakeCon(257.67), "CRWD", TS)
        self.assertEqual(spot, 257.67)
        self.assertEqual(src, "underlying_minutes_1m")

    def test_the_window_is_bounded_at_both_ends(self):
        # A bar from hours earlier is not "the price when you traded" -- it is a
        # different session's price wearing the right label. Returning nothing is
        # better than a plausible wrong number.
        con = FakeCon(1.0)
        A.spot_at(con, "CRWD", TS, max_lookback_min=45)
        _sym, hi, lo = con.asked[0]
        self.assertEqual(hi, TS)
        self.assertEqual(lo, TS - dt.timedelta(minutes=45))

    def test_no_bar_in_the_window_is_none_not_zero(self):
        # 0.0 is a price and would divide into an IV. None is no reading.
        self.assertEqual(A.spot_at(FakeCon(None), "CRWD", TS), (None, None))

    def test_a_null_close_is_also_no_reading(self):
        self.assertEqual(A.spot_at(FakeCon(None), "CRWD", TS)[0], None)


class FakeLog:
    """A log of record that records the UPDATEs it is asked to run."""

    def __init__(self, rows):
        self.rows, self.updates, self.select_sql = rows, [], None

    def execute(self, sql, params=None):
        if sql.strip().startswith("SELECT"):
            self.select_sql = sql
            return self
        self.updates.append((sql, params))
        return self

    def fetchall(self):
        return self.rows


class TestAttach(unittest.TestCase):
    def test_it_updates_only_the_fills_it_can_price(self):
        log = FakeLog([(1, "CRWD", TS), (2, "NBIS", TS)])
        n, tried = A.attach(FakeCon(257.67), log)
        self.assertEqual((n, tried), (2, 2))
        self.assertEqual(len(log.updates), 2)
        self.assertTrue(all("UPDATE fills" in u[0] for u in log.updates))
        self.assertEqual(log.updates[0][1], [257.67, 1], "spot then fill_id")

    def test_a_fill_the_archive_cannot_reach_is_left_alone(self):
        # No bar in the window means no UPDATE at all -- not an UPDATE to NULL, which
        # would erase a spot a better source had already written.
        log = FakeLog([(1, "CRWD", TS)])
        n, tried = A.attach(FakeCon(None), log)
        self.assertEqual((n, tried), (0, 1))
        self.assertEqual(log.updates, [])

    def test_the_candidate_query_is_the_one_the_caller_asked_for(self):
        log = FakeLog([])
        A.attach(FakeCon(1.0), log, where="underlying_px IS NULL AND broker = 'Schwab'")
        self.assertIn("underlying_px IS NULL AND broker = 'Schwab'", log.select_sql)


if __name__ == "__main__":
    unittest.main()
