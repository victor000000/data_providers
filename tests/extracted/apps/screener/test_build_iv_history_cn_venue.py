"""test_build_iv_history_cn_venue.py — the wiring between a venue and its frame parser.

The one thing that can go quietly wrong here is the mapping: SHFE and GFEX report the same
concept in DIFFERENT units (a fraction and a percent), so a parser pointed at the wrong
venue's frames yields an IV series that is 100x off and still looks like a number.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from data_providers.jobs.history import build_iv_history_cn_venue as entry  # noqa: E402
from data_providers.jobs.history.cn_iv import frames, venue  # noqa: E402
from data_providers.jobs.history.cn_iv.dump import SOLVER_NAMES  # noqa: E402


class TestWiring(unittest.TestCase):
    def test_every_venue_endpoint_has_a_parser(self):
        for v, endpoint in venue.VENUE_ENDPOINT.items():
            self.assertIn(endpoint, entry.POINT_FN, f"{v} -> {endpoint} has no parser")
            self.assertIn(v, venue.SOURCE_SUFFIX, f"{v} has no source suffix")

    def test_shfe_and_ine_frames_go_to_the_series_parser(self):
        self.assertIs(entry.POINT_FN[venue.VENUE_ENDPOINT["SHFE"]], frames.day_from_series_vol)
        self.assertIs(entry.POINT_FN[venue.VENUE_ENDPOINT["INE"]], frames.day_from_series_vol)

    def test_gfex_is_deliberately_absent_from_the_venue_path(self):
        """`option_hist_gfex` answers the first ~20 requests and then refuses every one at any
        pace, and ALL 105,872 LC/SI rows we hold came from Sina. Listing it here bought nothing
        and cost a full run of wasted requests; the solver path owns GFEX instead."""
        self.assertNotIn("GFEX", venue.VENUE_ENDPOINT)
        self.assertNotIn("GFEX", venue.SOURCE_SUFFIX)
        self.assertIn("GFEX", SOLVER_NAMES)

    def test_sources_are_named_per_product_and_distinct_per_venue(self):
        # A shared source name would let two venues' rows into one series. SHFE and INE share
        # an ENDPOINT but never a source name.
        names = {f"akshare_{p.lower()}_{venue.SOURCE_SUFFIX[v]}"
                 for v, p in (("SHFE", "CU"), ("SHFE", "AL"), ("INE", "SC"))}
        self.assertEqual(len(names), 3)


if __name__ == "__main__":
    unittest.main()
