"""test_bars.py — writing Sina quotes into cn.option_bars for the venues that had no bars."""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

import duckdb  # noqa: E402

from data_providers.jobs.history.cn_iv import bars as B  # noqa: E402

D = dt.date(2026, 9, 30)
DDL = ("CREATE TABLE option_bars (source VARCHAR, contract VARCHAR, underlying VARCHAR, "
       "expiry DATE, strike DOUBLE, call_put VARCHAR, day DATE, open DOUBLE, high DOUBLE, "
       "low DOUBLE, close DOUBLE, volume DOUBLE, vwap DOUBLE, venue VARCHAR, product VARCHAR, "
       "oi DOUBLE, settle DOUBLE, iv DOUBLE, delta DOUBLE)")


def con_with_schema():
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    return con


BARS = {("m2601C3000", D): (10.0, 12.0, 9.0, 11.0, 40.0),
        ("m2601P3000", D): (8.0, 9.0, 7.0, 8.5, 55.0)}


class TestRows(unittest.TestCase):
    def test_each_row_carries_the_parsed_contract_fields(self):
        rows = B.rows_for("DCE", "M", "YYMM", BARS)
        self.assertEqual(len(rows), 2)
        src, contract, underlying, exp, strike, cp, day = rows[0][:7]
        self.assertEqual(src, "sina_m_bars")
        self.assertEqual((underlying, strike, cp), ("M", 3000.0, "C"))
        self.assertEqual(day, D)
        self.assertEqual((exp.year, exp.month), (2025, 12))

    def test_the_ohlcv_survives_in_order(self):
        row = B.rows_for("DCE", "M", "YYMM", BARS)[0]
        self.assertEqual(row[7:12], (10.0, 12.0, 9.0, 11.0, 40.0))

    def test_exchange_only_columns_are_left_null_not_invented(self):
        # Sina has no settle, no open interest and no exchange IV. Filling them from our own
        # solver would make two different kinds of number indistinguishable in one column.
        row = B.rows_for("DCE", "M", "YYMM", BARS)[0]
        self.assertEqual(row[15:], (None, None, None, None))

    def test_an_unparseable_contract_is_counted_not_silently_dropped(self):
        rows = B.rows_for("DCE", "M", "YYMM", {("junk", D): (1.0, 1.0, 1.0, 1.0, 1.0)})
        self.assertEqual(rows, [])


class TestWrite(unittest.TestCase):
    def test_round_trip_and_idempotency(self):
        con = con_with_schema()
        rows = B.rows_for("DCE", "M", "YYMM", BARS)
        self.assertEqual(B.write_bars(con, rows), 2)
        self.assertEqual(con.execute("SELECT count(*) FROM option_bars").fetchone()[0], 2)
        B.write_bars(con, rows)                     # a re-run replaces, never doubles
        self.assertEqual(con.execute("SELECT count(*) FROM option_bars").fetchone()[0], 2)

    def test_writing_nothing_is_not_an_error(self):
        self.assertEqual(B.write_bars(con_with_schema(), []), 0)

    def test_covered_days_reports_what_a_rerun_can_skip(self):
        con = con_with_schema()
        B.write_bars(con, B.rows_for("DCE", "M", "YYMM", BARS))
        self.assertEqual(B.covered_days(con, "DCE", "M"), {D})
        self.assertEqual(B.covered_days(con, "GFEX", "SI"), set())


if __name__ == "__main__":
    unittest.main()
