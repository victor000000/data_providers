"""Tests for datasources/source_rank.py — which price source wins a disagreement."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.providers import source_rank


class TestRank(unittest.TestCase):
    def test_lower_is_better_and_tws_leads(self):
        # TWS is the direct broker feed and the only source with a genuine
        # two-sided book outside regular hours.
        self.assertEqual(source_rank.SOURCE_RANK["TWS"], 1)
        self.assertEqual(min(source_rank.SOURCE_RANK.values()), 1)

    def test_scraped_sources_rank_below_feeds(self):
        # yfinance produced the bogus TLT print (bid 82.92/ask 82.97 against its
        # own last of 77.11, and the same mid for SWKS). It must never outrank a
        # real feed.
        self.assertGreater(source_rank.SOURCE_RANK["yfinance"],
                           source_rank.SOURCE_RANK["TWS"])
        self.assertGreater(source_rank.SOURCE_RANK["yfinance"],
                           source_rank.SOURCE_RANK["Tiingo"])

    def test_futu_is_last(self):
        # Snapshot only, no book at all.
        self.assertEqual(source_rank.SOURCE_RANK["Futu"],
                         max(source_rank.SOURCE_RANK.values()))

    def test_every_rank_is_distinct(self):
        # Ties would make "which source wins" depend on dict order, which is
        # exactly the kind of silent arbitrariness this table exists to remove.
        ranks = list(source_rank.SOURCE_RANK.values())
        self.assertEqual(len(ranks), len(set(ranks)),
                         f"duplicate ranks: {sorted(ranks)}")

    def test_the_fetch_module_agrees_with_this_table(self):
        # The whole point of extracting it: build_positions used to hand-copy
        # this table with a "keep the two in step" comment. Assert the copy is
        # gone by checking the fetch module reads through to here.
        from data_providers.jobs.data_ops import fetch_extended_hours_prices as f
        self.assertIs(f.SOURCE_RANK, source_rank.SOURCE_RANK)


class TestLiveMaxAge(unittest.TestCase):
    def test_age_is_fifteen_minutes(self):
        self.assertEqual(source_rank.LIVE_MAX_AGE_MINUTES, 15)

    def test_the_fetch_module_derives_its_timedelta_from_here(self):
        # One number, not two: fetch's staleness gate and spot_for's freshness
        # ordering must mean the same age, or a quote can be simultaneously
        # real-time for one and stale for the other.
        import datetime as dt
        from data_providers.jobs.data_ops import fetch_extended_hours_prices as f
        self.assertEqual(f.LIVE_MAX_AGE,
                         dt.timedelta(minutes=source_rank.LIVE_MAX_AGE_MINUTES))


if __name__ == "__main__":
    unittest.main()
