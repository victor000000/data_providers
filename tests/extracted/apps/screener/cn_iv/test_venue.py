"""test_venue.py — the day-walk: what counts as a day, and when to give up on a source."""
import datetime as dt
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

from data_providers.jobs.history.cn_iv import venue  # noqa: E402


def _fast(cls):
    """Retire the two things that make this slow and test nothing.

    `retry` sleeps base**i between attempts and `throttle` waits on a gate file shared by
    every process on the box -- both correct in production, and in a unit test they turn a
    40-day chunk into 40 seconds of sleeping. Patching them keeps the logic under test.
    """
    for name, replacement in (("retry", lambda fn, *a, **k: fn()),
                              ("throttle", lambda *a, **k: None)):
        p = mock.patch.object(venue, name, replacement)
        p.start()
        cls.addClassCleanup(p.stop)
    return cls


def frame_fn(fail_days=(), empty_days=()):
    """A fake endpoint: {'隐含波动率': [0.2], '成交量': [1]} except where told otherwise."""
    def fn(symbol, trade_date):
        d = dt.date.fromisoformat(trade_date)
        if d in fail_days:
            raise RuntimeError("refused")
        if d in empty_days:
            return []
        return {"隐含波动率": [0.25], "成交量": [7]}
    return fn


POINT = lambda f: f["隐含波动率"][0]        # noqa: E731 -- the real parsers are tested in test_frames


class DayCount(unittest.TestCase):
    def test_weekdays_only_and_holidays_are_not_excluded(self):
        # CN holidays are answered with an empty frame, which is a skip -- not a reason to
        # guess at the exchange calendar here.
        days = venue.weekdays(dt.date(2026, 9, 24), dt.date(2026, 9, 30))
        self.assertNotIn(dt.date(2026, 9, 26), days)      # Saturday
        self.assertIn(dt.date(2026, 9, 28), days)         # Monday, holiday or not


@_fast
class FetchDays(unittest.TestCase):
    def test_empty_frame_is_a_skip_not_a_failure(self):
        days = [dt.date(2026, 9, 28), dt.date(2026, 9, 29)]
        pts, failed, unfetched = venue.fetch_days(
            frame_fn(empty_days={dt.date(2026, 9, 29)}), "X", days, POINT, 2)
        self.assertEqual(list(pts), [dt.date(2026, 9, 28)])
        self.assertEqual(failed, [])
        self.assertEqual(unfetched, 0)

    def test_a_wall_of_failures_aborts_and_says_how_much_is_left(self):
        # The GFEX lesson: after a source starts refusing, every further request is wasted.
        days = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(120)]
        days = [d for d in days if d.weekday() < 5]
        pts, failed, unfetched = venue.fetch_days(
            frame_fn(fail_days=set(days)), "X", days, POINT, 4)
        self.assertEqual(pts, {})
        self.assertGreater(unfetched, 0)
        self.assertLessEqual(len(failed), venue.CHUNK_DAYS,
                             "it should stop after the first refused chunk, not grind on")

    def test_a_few_failures_do_not_abort(self):
        days = [dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(5)]
        days = [d for d in days if d.weekday() < 5]
        pts, failed, unfetched = venue.fetch_days(
            frame_fn(fail_days={days[0]}), "X", days, POINT, 2)
        self.assertEqual(failed, [(days[0], "RuntimeError")])
        self.assertEqual(unfetched, 0)
        self.assertEqual(len(pts), len(days) - 1)


class Report(unittest.TestCase):
    def test_refusal_names_the_worker_count_remedy(self):
        msg = venue.report("GFEX", "LC", {}, [("d", "RuntimeError")], 300)
        self.assertIn("REFUSED", msg)
        self.assertIn("--workers", msg)

    def test_short_series_is_called_short(self):
        # A day lost to a timeout must not read as a complete series.
        msg = venue.report("SHFE", "CU", {dt.date(2026, 1, 2): 0.2}, [("d", "RuntimeError")], 0)
        self.assertIn("SHORT", msg)

    def test_a_clean_run_just_counts(self):
        msg = venue.report("SHFE", "CU", {dt.date(2026, 1, 2): 0.2}, [], 0)
        self.assertIn("1 points", msg)


if __name__ == "__main__":
    unittest.main()
