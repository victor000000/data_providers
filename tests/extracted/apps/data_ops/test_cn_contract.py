"""test_cn_contract.py — a contract code resolves to the right product, side, strike and DATE.

The date assertions are the point. `parse_contract` used to end every contract at the last
calendar day of the month before delivery, which no venue uses, and because `expiry` was absent
from the writer's DO UPDATE SET only newly-inserted rows carried the error — so MA701 lived
under 2026-12-10 (backfilled rows) and 2026-12-31 (every row written since) at once.
"""
import datetime as dt
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.data_ops import cn_contract  # noqa: E402
from data_providers.jobs.data_ops.cn_expiry import expiry_for  # noqa: E402


class TestParseContract(unittest.TestCase):
    def test_czce_single_digit_year_resolves_against_this_decade(self):
        prod, exp, cp, K = cn_contract.parse_contract("MA701C3350")
        self.assertEqual((prod, cp, K), ("MA", "C", 3350.0))
        self.assertEqual(exp, dt.date(2026, 12, 11))       # CZCE: 3 back from 15 Dec, not 31 Dec

    def test_every_held_series_lands_on_the_expiry_the_book_uses(self):
        for code in ("MA701C3350", "MA701P2900", "TA701C7400", "TA701P5900", "SR701C5800"):
            with self.subTest(code=code):
                self.assertEqual(cn_contract.parse_contract(code)[1], dt.date(2026, 12, 11))

    def test_the_decade_rolls_forward_rather_than_landing_twenty_years_back(self):
        """Reading YMM as YYMM once put 152,304 CZCE rows in 2004-2007. A digit resolving more
        than 5 years back belongs to the NEXT decade; one that does not keeps its identity, so
        an expired MA701 seen in 2029 stays 2027 rather than jumping to 2037."""
        with mock.patch.object(cn_contract.clock, "today", return_value=dt.date(2029, 5, 1)):
            rolled = cn_contract.parse_contract("MA101C3350")[1]      # digit says 2021
            kept = cn_contract.parse_contract("MA701C3350")[1]        # digit says 2027, expired
        self.assertEqual(rolled, expiry_for("CZCE", 2031, 1))
        self.assertGreater(rolled, dt.date(2029, 5, 1))               # not the past decade
        self.assertEqual(kept, dt.date(2026, 12, 11))

    def test_venue_overrides_the_format_guess(self):
        """SC2612C880 is CZCE-shaped but settles on INE's calendar, and MO2610P7400 has no
        dashes at all so it matches the CZCE pattern despite being CFFEX."""
        self.assertEqual(cn_contract.parse_contract("sc2612C880", "INE")[1], dt.date(2026, 11, 16))
        self.assertEqual(cn_contract.parse_contract("MO2610P7400", "CFFEX")[1],
                         dt.date(2026, 10, 16))            # third Friday of the CONTRACT month
        self.assertNotEqual(cn_contract.parse_contract("MO2610P7400", "CFFEX")[1],
                            cn_contract.parse_contract("MO2610P7400")[1])

    def test_four_digit_yymm_is_not_shifted(self):
        self.assertEqual(cn_contract.parse_contract("sc2612C880", "INE")[0:1], ("SC",))
        self.assertEqual(cn_contract.parse_contract("sc2612C880", "INE")[3], 880.0)

    def test_unparseable_is_none_rather_than_a_guess(self):
        for junk in ("", "MA", "3350C", "MA701X3350", None):
            with self.subTest(code=junk):
                self.assertIsNone(cn_contract.parse_contract(junk))


if __name__ == "__main__":
    unittest.main()
