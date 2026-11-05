"""test_calendar.py — month codes and expiries, and the tie to the weekday-only rules."""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

from data_providers.jobs.data_ops.cn_expiry import expiry_for  # noqa: E402
from data_providers.jobs.history.cn_iv import calendar  # noqa: E402


def weekday_cal(d0=dt.date(2024, 1, 1), d1=dt.date(2027, 12, 31)):
    out, d = [], d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


class TestExpiryOf(unittest.TestCase):
    def test_agrees_with_the_weekday_rule_on_a_holiday_free_calendar(self):
        # Two implementations exist for a reason (one runs inside a parse loop with no
        # network, this one wants the exchange's real trading days), so the thing worth
        # pinning is that they cannot DRIFT: with no holidays they must agree exactly.
        cal = weekday_cal()
        for venue in ("CZCE", "SHFE", "DCE", "GFEX", "CFFEX"):
            for y, m in ((2025, 5), (2025, 9), (2026, 1), (2026, 12)):
                self.assertEqual(calendar.expiry_of(venue, y, m, cal),
                                 expiry_for(venue, y, m), f"{venue} {y}-{m:02d}")

    def test_a_holiday_shifts_the_answer_that_the_weekday_rule_gets_wrong(self):
        # Qingming 2025 (Apr 4) sits inside m2505's window: counting weekdays lands on
        # 04-16, counting real trading days on the measured 04-17.
        cal = [d for d in weekday_cal() if d != dt.date(2025, 4, 4)]
        self.assertEqual(calendar.expiry_of("DCE", 2025, 5, cal), dt.date(2025, 4, 17))
        self.assertEqual(expiry_for("DCE", 2025, 5), dt.date(2025, 4, 16))

    def test_an_empty_calendar_falls_back_instead_of_returning_none(self):
        # A product whose month predates the calendar must still get SOME expiry -- the
        # caller divides by it.
        self.assertIsInstance(calendar.expiry_of("DCE", 2025, 5, []), dt.date)


class TestMonthCodes(unittest.TestCase):
    def test_window_reaches_back_from_each_expiry(self):
        cal = weekday_cal()
        got = calendar.month_codes(dt.date(2025, 1, 2), dt.date(2025, 12, 31), "DCE", 75, cal)
        self.assertTrue(got)
        for y, m, e in got:
            self.assertGreaterEqual(e, dt.date(2025, 1, 2))
            self.assertLessEqual(e - dt.timedelta(days=75), dt.date(2025, 12, 31))

    def test_months_are_consecutive_and_end_in_a_real_expiry(self):
        cal = weekday_cal()
        got = calendar.month_codes(dt.date(2025, 1, 2), dt.date(2025, 6, 30), "SHFE", 75, cal)
        months = [(y, m) for y, m, _e in got]
        self.assertEqual(months, sorted(months))
        for _y, _m, e in got:
            self.assertLess(e.weekday(), 5)


if __name__ == "__main__":
    unittest.main()
