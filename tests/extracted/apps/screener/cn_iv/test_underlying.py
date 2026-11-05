"""test_underlying.py — reading the thing the CN options are written on.

The value of this module is entirely in what it REFUSES to store: CN frames use empty strings
and dashes for a missing number, pandas turns those into NaN, and NaN is truthy -- so the
obvious `if close:` guard stores it as a price, and DuckDB then sorts that row above every
real one.
"""
import datetime as dt
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

import pandas as pd  # noqa: E402

from data_providers.jobs.history.cn_iv import underlying as U  # noqa: E402


class FakeAk:
    def futures_main_sina(self, symbol):
        return pd.DataFrame({
            "日期": ["2026-09-29", "2026-09-30", "2026-10-01"],
            "开盘价": ["3,000.0", "--", 3010.0],
            "最高价": [3050.0, None, 3060.0],
            "最低价": [2990.0, None, 3000.0],
            "收盘价": [3012.0, 3020.0, None],
            "成交量": [1000, 2000, 3000],
            "持仓量": [500, 600, 700],
            "动态结算价": [3011.0, 3019.0, None],
        })

    def stock_zh_index_daily(self, symbol):
        return pd.DataFrame({"date": ["2026-09-30"], "close": [6200.0], "volume": [None]})


def _fast(cls):
    p = mock.patch.object(U, "retry", lambda fn, *a, **k: fn())
    p.start()
    cls.addClassCleanup(p.stop)
    return cls


@_fast
class TestMainSeries(unittest.TestCase):
    def test_rows_are_in_schema_order(self):
        rows = U.main_series(FakeAk(), "M")
        self.assertEqual(rows[0], (dt.date(2026, 9, 29), 3000.0, 3050.0, 2990.0, 3012.0,
                                   3011.0, 1000.0, 500.0))

    def test_a_missing_close_drops_the_day_rather_than_storing_NaN(self):
        # 2026-10-01 has no close at all -- an empty cell, not a zero price.
        days = [r[0] for r in U.main_series(FakeAk(), "M")]
        self.assertEqual(days, [dt.date(2026, 9, 29), dt.date(2026, 9, 30)])

    def test_a_dash_open_becomes_None_not_zero(self):
        row = U.main_series(FakeAk(), "M")[1]
        self.assertIsNone(row[1])          # open was '--'
        self.assertEqual(row[4], 3020.0)   # close survived

    def test_nan_is_not_a_price(self):
        self.assertIsNone(U.num(float("nan")))
        self.assertIsNone(U.num("--"))
        self.assertIsNone(U.num(None))
        self.assertEqual(U.num("1,234.5"), 1234.5)


@_fast
class TestIndexSeries(unittest.TestCase):
    def test_index_rows_have_no_open_high_low_settle_or_oi(self):
        # An index is not a traded contract: inventing an open or a settlement for it would
        # put a futures-shaped row in the table under an index's name.
        row = U.index_series(FakeAk())[0]
        self.assertEqual((row[0], row[4]), (dt.date(2026, 9, 30), 6200.0))
        for i in (1, 2, 3, 5, 7):
            self.assertIsNone(row[i])


if __name__ == "__main__":
    unittest.main()
