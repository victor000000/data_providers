"""The account-state parsers: the broker's own position list and the summary."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import schwab_account_book as B
from tests.extracted.apps.trade_log._statement import sections


class TestPositions(unittest.TestCase):
    def setUp(self):
        self.rows = B.parse_positions(sections()["Options"])

    def test_the_totals_row_is_not_a_position(self):
        # `,OVERALL TOTALS,,,...` sits in the same column as a symbol and has no expiry,
        # so a parser that only checks for a non-empty symbol appends a phantom leg.
        syms = [r[5] for r in self.rows]
        self.assertNotIn("OVERALL", " ".join(syms))
        self.assertEqual(len(self.rows), 2)

    def test_the_key_matches_the_other_tables(self):
        # The statement prints `TLT261120P74`; every other table uses 8-digit padding.
        self.assertIn("TLT261120P00074000", [r[5] for r in self.rows])

    def test_it_carries_the_brokers_own_quantity(self):
        got = {r[5]: r[10] for r in self.rows}
        self.assertEqual(got["TLT261120P00074000"], -7.0)
        self.assertEqual(got["CRWD261218P00240000"], -6.0)


class TestSummary(unittest.TestCase):
    def test_net_liquidating_value_is_read(self):
        got = B.parse_summary(sections()["Account Summary"])
        self.assertEqual(got["Net Liquidating Value"], 65406.20)

    def test_a_parenthesised_buying_power_is_negative(self):
        # The account is over-allocated and Schwab prints the shortfall in parentheses.
        # Positive here would read as buying power the account does not have.
        got = B.parse_summary(sections()["Account Summary"])
        self.assertEqual(got["Option Buying Power"], -233.33)


if __name__ == "__main__":
    unittest.main()
