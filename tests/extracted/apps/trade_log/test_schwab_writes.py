"""Writing a statement's four sections -- the guards that keep it re-runnable.

`executemany` RAISES on an empty list rather than doing nothing -- the same crash that
destroyed MA's flow-days in build_option_flow -- and a quiet day with no orders is the
normal case here. The guards must also live in the SAME function as the calls they
guard: check_regressions.py searches for one only up to the nearest FunctionDef, so a
guard hoisted to the caller reads as missing. Row shape is in test_schwab_writes_shape.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import schwab_writes as W
from tests.extracted.apps.trade_log._writes_fixtures import EMPTY_SUMMARY, FakeCon, fill, order


def writes(con):
    return [c for c in con.calls if c[0] == "executemany"]


class TestEmptyInputNeverReachesExecutemany(unittest.TestCase):
    def test_no_fills_and_no_orders_writes_nothing(self):
        con = FakeCon()
        W.write_account(con, [], [], [], EMPTY_SUMMARY)
        self.assertEqual(writes(con), [])

    def test_a_fill_list_that_is_all_duplicates_writes_nothing(self):
        # Dedupe runs before the guard has anything to do: a re-run over a directory
        # that is already loaded must not re-insert, and must not call executemany([]).
        con = FakeCon(already=["STRANGLE x; net .85; BUY 82C"])
        W.write_account(con, [fill()], [], [], EMPTY_SUMMARY)
        self.assertEqual(writes(con), [])

    def test_an_all_duplicate_order_list_writes_nothing(self):
        con = FakeCon(already=["STRANGLE x; FILLED"])
        W.write_account(con, [], [order()], [], EMPTY_SUMMARY)
        self.assertEqual(writes(con), [])


class TestTheDailyRow(unittest.TestCase):
    def test_a_summary_without_a_net_liq_writes_no_row(self):
        con = FakeCon()
        W.write_account(con, [], [], [], {"Option Buying Power": -233.33})
        self.assertEqual([c for c in con.calls
                          if c[0] == "execute" and "account_daily" in c[1]], [])

    def test_the_net_liq_is_written(self):
        con = FakeCon()
        W.write_account(con, [], [], [], {"Net Liquidating Value": 65406.20})
        self.assertTrue([c for c in con.calls
                         if c[0] == "execute" and "account_daily" in c[1]])


class TestCounts(unittest.TestCase):
    def test_it_reports_what_it_wrote(self):
        got = W.write_account(FakeCon(), [fill()], [order()], [], EMPTY_SUMMARY)
        self.assertEqual(got["fills"], 1)
        self.assertEqual(got["orders"], 1)

    def test_nothing_new_reports_zero(self):
        got = W.write_account(FakeCon(already=["STRANGLE x; net .85; BUY 82C"]),
                              [fill()], [], [], EMPTY_SUMMARY)
        self.assertEqual(got["fills"], 0)


if __name__ == "__main__":
    unittest.main()
