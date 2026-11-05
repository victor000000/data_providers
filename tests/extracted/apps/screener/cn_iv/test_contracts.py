"""test_contracts.py — reading one contract's history off Sina, in both shapes it is used.

The two readers share a request but not a return: the solver wants (close, volume) per day,
the bars path wants full OHLC. A venue that serves one and not the other is normal here --
Sina has no settle, no open interest and no exchange IV column at all.
"""
import datetime as dt
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

import pandas as pd  # noqa: E402

from data_providers.jobs.history.cn_iv import contracts  # noqa: E402


class FakeAk:
    def option_commodity_hist_sina(self, symbol):
        return pd.DataFrame({"date": ["2026-09-29", "2026-09-30", "2026-10-01"],
                             "open": [10.0, 11.0, None], "high": [12.0, 13.0, None],
                             "low": [9.0, 10.0, None], "close": [11.0, 0.0, None],
                             "volume": [40, 0, None]})

    def option_cffex_zz1000_daily_sina(self, symbol):
        return pd.DataFrame({"date": ["2026-09-30"], "open": [50.0], "high": [60.0],
                             "low": [45.0], "close": [55.0], "volume": [7]})


def _fast(cls):
    for name, replacement in (("retry", lambda fn, *a, **k: fn()),
                              ("throttle", lambda *a, **k: None)):
        p = mock.patch.object(contracts, name, replacement)
        p.start()
        cls.addClassCleanup(p.stop)
    return cls


@_fast
class TestBars(unittest.TestCase):
    def test_full_ohlcv_in_order(self):
        rows = contracts.contract_bars(FakeAk(), "DCE", "m2601C3000")
        self.assertEqual(rows[0], (dt.date(2026, 9, 29), 10.0, 12.0, 9.0, 11.0, 40.0))

    def test_a_zero_close_day_is_dropped(self):
        # Sina keeps a row with close 0 for a contract that did not trade; storing it would
        # put a price of zero into a series everything downstream treats as a price.
        rows = contracts.contract_bars(FakeAk(), "DCE", "m2601C3000")
        self.assertEqual([r[0] for r in rows], [dt.date(2026, 9, 29)])

    def test_cffex_reads_the_index_option_endpoint(self):
        self.assertEqual(contracts.contract_bars(FakeAk(), "CFFEX", "mo2610C6000"),
                         [(dt.date(2026, 9, 30), 50.0, 60.0, 45.0, 55.0, 7.0)])

    def test_an_empty_frame_is_no_rows_not_an_error(self):
        class Empty(FakeAk):
            def option_commodity_hist_sina(self, symbol):
                return pd.DataFrame()
        self.assertEqual(contracts.contract_bars(Empty(), "DCE", "x"), [])


@_fast
class TestHist(unittest.TestCase):
    def test_returns_close_and_volume_per_day(self):
        got = contracts.contract_hist(FakeAk(), "DCE", "m2601C3000")
        self.assertEqual(got, {dt.date(2026, 9, 29): (11.0, 40.0)})

    def test_the_two_readers_agree_on_which_days_exist(self):
        # They are the same request; a day the solver solves but the bars path drops (or the
        # reverse) would make the stored chain disagree with the stored IV.
        ak = FakeAk()
        bars = {r[0] for r in contracts.contract_bars(ak, "DCE", "x")}
        hist = set(contracts.contract_hist(ak, "DCE", "x"))
        self.assertEqual(bars, hist)


if __name__ == "__main__":
    unittest.main()
