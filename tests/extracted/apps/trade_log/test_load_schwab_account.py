"""The loader end to end, against a literal statement and a fake log.

This is the module that did not exist, so two statements -- one of them a whole trading
session -- sat unread in `../trading_logs` with nothing to load them.
"""
import csv
import datetime as dt
import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import load_schwab_account as L
from data_providers.jobs.trades import schwab_statement as S
from tests.extracted.apps.trade_log._statement import STATEMENT

NAIVE = dt.datetime(2026, 10, 6, 21, 50, 47)


class FakeCon:
    """A log holding exactly one of the statement's fills, at UTC."""

    def __init__(self, utc=None):
        self.utc = utc

    def execute(self, sql, params=None):
        return self

    def fetchone(self):
        return (self.utc,)


class TestTheWholeStatement(unittest.TestCase):
    def setUp(self):
        rows = list(csv.reader(io.StringIO(STATEMENT), strict=False))
        self.sec = S.sections(rows)
        self.offset = dt.timedelta(hours=8)

    def test_the_trade_history_is_parsed_into_both_legs_of_each_spread(self):
        from data_providers.jobs.trades import schwab_account_parse as P
        fills = P.parse_fills(self.sec["Account Trade History"], self.offset)
        self.assertEqual(len(fills), 4)

    def test_the_order_history_is_not_empty(self):
        from data_providers.jobs.trades import schwab_account_parse as P
        self.assertTrue(P.parse_orders(self.sec["Account Order History"], self.offset))

    def test_the_options_table_is_parsed(self):
        from data_providers.jobs.trades import schwab_account_book as B
        self.assertEqual(len(B.parse_positions(self.sec["Options"])), 2)

    def test_the_summary_is_parsed(self):
        from data_providers.jobs.trades import schwab_account_book as B
        got = B.parse_summary(self.sec["Account Summary"])
        self.assertEqual(got["Net Liquidating Value"], 65406.20)

    def test_the_timestamps_are_utc_not_the_export_clock(self):
        # THE bug the clock module exists for: parsed at offset 0 and written as-is,
        # this 13:50 UTC fill would be filed as 21:50 and land on the wrong day at the
        # month boundary.
        from data_providers.jobs.trades import schwab_account_parse as P
        fills = P.parse_fills(self.sec["Account Trade History"], self.offset)
        self.assertTrue(all(f["ts_utc"].hour < 20 for f in fills),
                        "a fill parsed outside the US session window is the wrong clock")


class TestTheDirectoryGlob(unittest.TestCase):
    def test_it_matches_the_account_statement_flavour(self):
        # Not PositionStatement and not TradeActivity: those have their own loader, and
        # a glob that catches them too would double-load the same trades.
        import fnmatch
        self.assertTrue(fnmatch.fnmatch("2026-10-06-AccountStatement.csv",
                                        "*AccountStatement.csv"))
        self.assertFalse(fnmatch.fnmatch("2026-10-05-PositionStatement.csv",
                                         "*AccountStatement.csv"))


if __name__ == "__main__":
    unittest.main()
