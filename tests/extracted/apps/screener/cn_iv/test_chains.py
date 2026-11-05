"""test_chains.py — Sina chain access: one request per contract, and never a zero price."""
import datetime as dt
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

import pandas as pd  # noqa: E402

from data_providers.jobs.history.cn_iv import chains, fetch  # noqa: E402


class FakeAk:
    """Only the five Sina calls chains.py makes, with the frames they really return."""

    def stock_zh_index_daily(self, symbol):
        return pd.DataFrame({"date": ["2026-09-29", "2026-09-30"], "close": [6100.0, 6200.0]})

    def futures_main_sina(self, symbol):
        return pd.DataFrame({"日期": ["2026-09-29", "2026-09-30"],
                             "收盘价": ["3,000.0", "--"]})

    def option_commodity_contract_table_sina(self, symbol, contract):
        if contract == "m2510":                     # not a listed 豆粕 month
            raise KeyError("up")
        return pd.DataFrame({"行权价": [3000, 3050], "看涨合约-看涨期权合约": ["m2601C3000", "m2601C3050"],
                             "看跌合约-看跌期权合约": ["m2601P3000", "m2601P3050"]})

    def option_cffex_zz1000_spot_sina(self, symbol):
        return pd.DataFrame({"行权价": [6000], "看涨合约-标识": ["mo2610C6000"],
                             "看跌合约-标识": ["mo2610P6000"]})

    def option_commodity_hist_sina(self, symbol):
        return pd.DataFrame({"date": ["2026-09-30", "2026-10-01"], "close": [12.5, 0.0],
                             "volume": [30, 0]})

    def option_cffex_zz1000_daily_sina(self, symbol):
        return pd.DataFrame({"date": ["2026-09-30"], "close": [55.0], "volume": [7]})


def _fast(cls):
    for name, replacement in (("retry", lambda fn, *a, **k: fn()),
                              ("throttle", lambda *a, **k: None)):
        p = mock.patch.object(chains, name, replacement)
        p.start()
        cls.addClassCleanup(p.stop)
    return cls


@_fast
class TestUnderlyingPath(unittest.TestCase):
    def test_dce_main_future_skips_a_dash_price(self):
        got = chains.underlying_path(FakeAk(), "DCE", "M")
        self.assertEqual(got, {dt.date(2026, 9, 29): 3000.0})   # "--" is not 0.0

    def test_cffex_uses_the_index_not_a_future(self):
        self.assertEqual(chains.underlying_path(FakeAk(), "CFFEX", "MO"),
                         {dt.date(2026, 9, 29): 6100.0, dt.date(2026, 9, 30): 6200.0})


@_fast
class TestChainTable(unittest.TestCase):
    def test_returns_the_ladder_with_both_codes(self):
        got = chains.chain_table(FakeAk(), "DCE", "豆粕期权", "m2601")
        self.assertEqual(got, [(3000.0, "m2601C3000", "m2601P3000"),
                               (3050.0, "m2601C3050", "m2601P3050")])

    def test_an_unlisted_month_raises_and_that_is_the_answer(self):
        # Six of twelve months are unlisted for 豆粕. The caller skips on this; it must not
        # be swallowed here, or an unlisted month would look like an empty chain. Since
        # 2026-10-08 it raises fetch.Unlisted specifically, so the caller can tell it apart
        # from a request that went UNANSWERED -- which is a hole, not a skip.
        with self.assertRaises(fetch.Unlisted):
            chains.chain_table(FakeAk(), "DCE", "豆粕期权", "m2510")


if __name__ == "__main__":
    unittest.main()
