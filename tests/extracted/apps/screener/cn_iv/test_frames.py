"""test_frames.py — the CN frame parsers: one exchange day in, one ATM IV out."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

import pandas as pd  # noqa: E402

from data_providers.jobs.history.cn_iv.frames import day_from_contract_hist, day_from_series_vol, num  # noqa: E402


class TestNum(unittest.TestCase):
    def test_missing_forms_all_mean_none(self):
        # CN frames use empty strings and dashes for "no value", never a bare null.
        for v in (None, "", "  ", "-", "--", "abc"):
            self.assertIsNone(num(v), v)

    def test_nan_is_not_a_number(self):
        # NaN would otherwise be STORED -- DuckDB sorts it above every number -- so it has to
        # read as "no value" here, not survive the parse.
        self.assertIsNone(num(float("nan")))
        self.assertIsNone(num(float("inf")))

    def test_plain_numbers(self):
        self.assertEqual(num("1,234.5"), 1234.5)
        self.assertEqual(num(" 0.2046 "), 0.2046)


class TestSeriesVol(unittest.TestCase):
    def test_most_traded_series_wins(self):
        df = pd.DataFrame({"合约系列": ["cu2611", "cu2612"], "成交量": [79622, 8576],
                           "隐含波动率": [0.204635, 0.199282]})
        self.assertAlmostEqual(day_from_series_vol(df), 0.204635)

    def test_an_impossible_iv_is_dropped_not_stored(self):
        df = pd.DataFrame({"合约系列": ["a", "b"], "成交量": [10, 99],
                           "隐含波动率": [0.2, 640.0]})     # 640 as a fraction is nonsense
        self.assertAlmostEqual(day_from_series_vol(df), 0.2)   # falls back to the sane row

    def test_unrecognised_frame_is_none_not_a_crash(self):
        self.assertIsNone(day_from_series_vol(pd.DataFrame({"x": [1]})))


class TestContractHist(unittest.TestCase):
    def _frame(self):
        # si2611 most-traded; ATM is 10000 (smallest |C-P|); GFEX reports PERCENT.
        return pd.DataFrame({
            "合约名称": ["si2611-C-10000", "si2611-P-10000", "si2611-C-10400",
                         "si2611-P-10400", "si2612-C-10000", "si2612-P-10000"],
            "成交量": [500, 400, 6, 5, 1, 1],
            "结算价": [120.0, 110.0, 30.0, 90.0, 200.0, 50.0],
            "隐含波动率": [31.0, 33.0, 38.0, 39.0, 20.0, 20.0],
        })

    def test_percent_is_converted_to_fraction(self):
        iv = day_from_contract_hist(self._frame())
        self.assertAlmostEqual(iv, 0.32, places=6)      # mean(31, 33) / 100

    def test_parity_picks_the_strike_nearest_the_future(self):
        df = self._frame()
        df.loc[df["合约名称"] == "si2611-C-10000", "结算价"] = 900.0   # 10000 now far off
        df.loc[df["合约名称"] == "si2611-P-10000", "结算价"] = 10.0
        # |C-P| at 10000 is 890; at 10400 it is 60 -> 10400 wins, mean(38, 39)/100
        self.assertAlmostEqual(day_from_contract_hist(df), 0.385, places=6)

    def test_a_month_with_no_matching_strikes_is_none(self):
        df = self._frame()
        df["合约名称"] = df["合约名称"].str.replace("-P-", "-X-")
        self.assertIsNone(day_from_contract_hist(df))


if __name__ == "__main__":
    unittest.main()
