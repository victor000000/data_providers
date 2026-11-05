"""The column/value shape of each INSERT schwab_writes runs.

A drift here is invisible to the gate -- compile cannot see SQL -- and surfaces as a
BinderException at run time, if at all.
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.jobs.trades import schwab_writes as W
from tests.extracted.apps.trade_log._writes_fixtures import EMPTY_SUMMARY, FakeCon, fill, order


class TestTheRowShape(unittest.TestCase):
    def test_a_fill_row_matches_its_column_list(self):
        # A column/value drift here is invisible to the gate -- compile cannot see SQL --
        # and surfaces as a BinderException at run time, if at all.
        con = FakeCon()
        W.write_account(con, [fill()], [], [], EMPTY_SUMMARY)
        sql, rows = [(c[1], c[2]) for c in con.calls if c[0] == "executemany"][0]
        cols = [c.strip() for c in W.FILL_COLS.replace("\n", " ").split(",")]
        self.assertEqual(len(rows[0]), len(cols), "fill values do not match FILL_COLS")
        self.assertEqual(len(rows[0]), sql.count("?"), "values do not match placeholders")

    def test_the_values_line_up_with_the_columns(self):
        con = FakeCon()
        W.write_account(con, [fill()], [], [], EMPTY_SUMMARY)
        cols = [c.strip() for c in W.FILL_COLS.replace("\n", " ").split(",")]
        row = dict(zip(cols, [(c[2])[0] for c in con.calls if c[0] == "executemany"][0]))
        self.assertEqual(row["ts_utc"], dt.datetime(2026, 10, 6, 13, 50))
        self.assertEqual(row["underlying"], "TLT")
        self.assertEqual(row["symbol"], "TLT261120C00082000")
        self.assertEqual(row["price"], 0.27)
        self.assertEqual(row["source"], W.SOURCE)

    def test_an_order_row_matches_its_column_list(self):
        con = FakeCon()
        W.write_account(con, [], [order()], [], EMPTY_SUMMARY)
        sql, rows = [(c[1], c[2]) for c in con.calls if c[0] == "executemany"][0]
        cols = [c.strip() for c in W.ORDER_COLS.replace("\n", " ").split(",")]
        self.assertEqual(len(rows[0]), len(cols))
        self.assertEqual(len(rows[0]), sql.count("?"))

    def test_a_rejected_order_keeps_its_status(self):
        con = FakeCon()
        W.write_account(con, [], [dict(order(), status="REJECTED")], [], EMPTY_SUMMARY)
        rows = [c[2] for c in con.calls if c[0] == "executemany"][0]
        cols = [c.strip() for c in W.ORDER_COLS.replace("\n", " ").split(",")]
        self.assertEqual(dict(zip(cols, rows[0]))["status"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
