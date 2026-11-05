"""A quote is live only while it is RECENT.

`quote_class` and `is_realtime` are stamped by the fetch when the quote is CAPTURED. A
pricing layer that reads them back later without looking at the clock reports what the
quote WAS, not what it is.

Measured 2026-10-06: `fetch_extended_hours_prices` last ran at 13:46 and TWS then went
down, so the 17:20 build priced four underlyings off a 214-minute-old quote -- every one
still labelled `live` with `is_realtime=True`. The report said "every underlying priced
in real time" while CRWD had moved 1.53% since the price the whole book was struck at.
The ordering clause in the query ranks recent quotes first; it cannot help when nothing
recent exists, and it never touched the label.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.providers import source_rank as s


class TestItAgesAQuote(unittest.TestCase):
    def test_a_recent_quote_stays_live(self):
        self.assertEqual(s.aged_class("live", True, 2), ("live", True))

    def test_the_boundary_itself_is_still_live(self):
        # The same number the fetch layer classifies with, so "real-time" cannot mean one
        # age in the fetch and another here.
        self.assertEqual(
            s.aged_class("live", True, s.LIVE_MAX_AGE_MINUTES), ("live", True))

    def test_a_minute_past_the_window_is_not(self):
        cls, rt = s.aged_class("live", True, s.LIVE_MAX_AGE_MINUTES + 1)
        self.assertEqual((cls, rt), ("live_stale", False))

    def test_the_measured_case(self):
        # 214 minutes old, from the run that found this.
        self.assertEqual(s.aged_class("live", True, 214), ("live_stale", False))


class TestItDoesNotOverreach(unittest.TestCase):
    def test_a_class_that_is_already_not_real_time_is_untouched(self):
        # Nothing to downgrade -- and overwriting `last_only` would lose the difference
        # between "a stale book" and "no book at all".
        for cls in ("last_only", "live_stale", "broker_snapshot"):
            self.assertEqual(s.aged_class(cls, False, 999), (cls, False))

    def test_an_unknown_age_leaves_the_label_alone(self):
        # None means the row carried no timestamp. Guessing staleness from a missing
        # field would relabel good quotes; the caller decides what a missing stamp means.
        self.assertEqual(s.aged_class("live", True, None), ("live", True))

    def test_it_never_promotes(self):
        # A quote the fetch called not-real-time must not become real-time by being fresh.
        self.assertEqual(s.aged_class("last_only", False, 0), ("last_only", False))


if __name__ == "__main__":
    unittest.main()
