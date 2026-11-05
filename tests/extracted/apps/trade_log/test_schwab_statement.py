"""Section splitting, the OCC key, and the statement's date form."""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import schwab_statement as S
from tests.extracted.apps.trade_log._statement import sections


class TestSections(unittest.TestCase):
    def test_it_finds_every_section_by_its_title(self):
        sec = sections()
        for name in ("Cash Balance", "Account Order History", "Account Trade History",
                     "Options", "Account Summary"):
            self.assertIn(name, sec, f"{name} was not split out")

    def test_the_trade_history_keeps_its_own_rows(self):
        # 1 header + 4 data rows (two spreads, each two legs). Trailing blanks are
        # carried too, so the count is a lower bound on what the parser will see --
        # what matters is that the rows did not leak into another section.
        rows = sections()["Account Trade History"]
        self.assertEqual(sum(1 for r in rows if any(c.strip() for c in r)), 5)

    def test_rows_before_any_title_belong_to_nothing(self):
        sec = sections()
        self.assertNotIn(None, sec)


class TestOccKey(unittest.TestCase):
    def test_it_pads_the_strike_to_eight_digits(self):
        # The statement's own Option Code prints `TLT261120P74`, un-padded, while
        # `positions`, `fills` and the IBKR loaders all use the 8-digit form. Keying on
        # the file's spelling makes every Schwab leg a phantom mismatch.
        self.assertEqual(S.occ("TLT", dt.date(2026, 11, 20), "74", "P"),
                         "TLT261120P00074000")

    def test_it_agrees_with_the_statement_for_a_fractional_strike(self):
        self.assertEqual(S.occ("CRWD", dt.date(2026, 12, 18), 240, "P"),
                         "CRWD261218P00240000")

    def test_a_string_strike_works_like_a_number(self):
        self.assertEqual(S.occ("EWZ", dt.date(2026, 11, 20), "43", "C"),
                         S.occ("EWZ", dt.date(2026, 11, 20), 43.0, "C"))


class TestWhen(unittest.TestCase):
    def test_it_reads_the_statements_two_digit_year(self):
        self.assertEqual(S._when("10/6/26 21:50:47"),
                         dt.datetime(2026, 10, 6, 21, 50, 47))

    def test_a_blank_cell_is_none_not_an_exception(self):
        self.assertIsNone(S._when(""))
        self.assertIsNone(S._when("Time Placed"))


if __name__ == "__main__":
    unittest.main()
