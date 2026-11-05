"""test_cn_expiry.py — the CN option expiry rules, pinned to measured last trading days.

Every date below was measured on 2026-10-07 from the contract's own history: the last day it
traded with a nonzero volume (Sina), read against `tool_trade_date_hist_sina`. The old rules
were 7-11 days early on SHFE and DCE, and because T enters every greek as sqrt(T) that is
~15% of IV on a front-month option -- a wrong P&L, not an error message.

SOME OF THESE ARE OFF BY A DAY, deliberately and visibly: the weekday counters skip weekends,
not CN holidays (April 2025 has Qingming). They are asserted as "within one day" rather than
as equality so that nobody later reads the exact values as the rule.
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from data_providers.jobs.data_ops.cn_expiry import expiry_for  # noqa: E402

# (venue, year, month) -> the last day the contract actually traded
MEASURED = {
    ("DCE", 2025, 9): dt.date(2025, 8, 18),
    ("DCE", 2026, 1): dt.date(2025, 12, 16),
    ("SHFE", 2025, 9): dt.date(2025, 8, 25),
    ("GFEX", 2025, 9): dt.date(2025, 8, 7),
    ("CFFEX", 2025, 6): dt.date(2025, 6, 20),
    ("CFFEX", 2025, 9): dt.date(2025, 9, 19),
}

# Holiday months: the weekday counter lands a day either side of the measurement.
ONE_DAY_OFF = {
    ("DCE", 2025, 5): dt.date(2025, 4, 17),      # Qingming
    ("DCE", 2026, 5): dt.date(2026, 4, 17),      # Qingming
    ("SHFE", 2025, 5): dt.date(2025, 4, 23),     # Qingming, a day the other way
    ("CZCE", 2026, 7): dt.date(2026, 6, 11),     # Dragon Boat
}


class TestExpiryFor(unittest.TestCase):
    def test_exact_venues(self):
        for (venue, y, m), want in MEASURED.items():
            self.assertEqual(expiry_for(venue, y, m), want,
                             f"{venue} {y}-{m:02d} should expire on its last traded day")

    def test_holiday_months_within_one_day(self):
        for (venue, y, m), want in ONE_DAY_OFF.items():
            got = expiry_for(venue, y, m)
            self.assertLessEqual(abs((got - want).days), 1,
                                 f"{venue} {y}-{m:02d}: {got} vs measured {want}")

    def test_shfe_and_dce_do_not_share_a_rule(self):
        # The bug was one rule applied per venue from memory. These two months were both
        # wrong BEFORE the fix and are still the clearest way to tell them apart: a
        # 5th-to-last-trading-day rule and a 12th-trading-day rule differ by a week.
        for y, m in ((2025, 9), (2026, 5)):
            self.assertNotEqual(expiry_for("SHFE", y, m), expiry_for("DCE", y, m))
        # And the direction: SHFE's is the later of the two in a 31-day prior month.
        self.assertGreater(expiry_for("SHFE", 2025, 9), expiry_for("DCE", 2025, 9))

    def test_always_in_the_month_before_delivery(self):
        for venue in ("CZCE", "SHFE", "DCE", "GFEX"):
            for y, m in ((2025, 1), (2025, 12), (2026, 7)):
                e = expiry_for(venue, y, m)
                py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
                self.assertEqual((e.year, e.month), (py, pm), f"{venue} {y}-{m:02d}")
                self.assertLess(e.weekday(), 5, f"{venue} {y}-{m:02d} landed on a weekend")

    def test_cffex_is_the_contract_month_not_the_prior_one(self):
        # The one venue whose expiry is in the DELIVERY month; treating it like the others
        # would move it a month, which is how MO2610 was stored as expiring on its own
        # last bar (DTE 0) instead of 2026-10-16.
        self.assertEqual(expiry_for("CFFEX", 2026, 10), dt.date(2026, 10, 16))

    def test_a_holiday_week_moves_the_expiry_and_the_calendar_knows_it(self):
        # Golden Week: October 2026 opens on the 8th, so DCE's 12th trading day is the 23rd
        # while counting weekdays says the 16th. The solver solved m2611 at 15.37% on the
        # right date while option_bars carried the 16th -- which prices the same contract at
        # 18.42%, because a week of T is ~15% of IV on a front month.
        cal = [d for d in (dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(120))
               if d.weekday() < 5 and not (d.month == 10 and d.day < 8)]
        self.assertEqual(expiry_for("DCE", 2026, 11, cal), dt.date(2026, 10, 23))
        self.assertEqual(expiry_for("DCE", 2026, 11), dt.date(2026, 10, 16))
        self.assertNotEqual(expiry_for("GFEX", 2026, 11, cal),
                            expiry_for("GFEX", 2026, 11))

    def test_a_calendar_that_does_not_reach_the_month_falls_back(self):
        # Short calendars must not turn a date into None: callers divide by it.
        self.assertEqual(expiry_for("DCE", 2026, 11, []), dt.date(2026, 10, 16))
        self.assertEqual(expiry_for("DCE", 2026, 11, [dt.date(2026, 1, 5)]),
                         dt.date(2026, 10, 16))

    def test_unknown_venue_still_answers(self):
        # A venue added to cn_product_map but not here must not raise inside a parse loop.
        got = expiry_for("NEWVENUE", 2025, 9)
        self.assertIsInstance(got, dt.date)


if __name__ == "__main__":
    unittest.main()
