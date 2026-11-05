"""Tests for the Nasdaq daily-bar adapter (datasources/nasdaq.py).
Two things fail silently otherwise: Nasdaq's numbers arrive as display strings ("$1,234.56",
so float() raises), and "no bars" has to stay distinguishable from "the request failed".
`requests.get` is patched -- these tests touch no network.
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from data_providers import runtime as bootstrap  # noqa: F401
from data_providers.providers import nasdaq  # noqa: E402


def payload(rows):
    return {"data": {"tradesTable": {"rows": rows}}}


def row(date="10/09/2026", close="10.5"):
    return dict(date=date, open="10", high="11", low="9", close=close, volume="1,000")


def fake_get(responses):
    """A requests.get stand-in yielding `responses` in order, repeating the last one."""
    calls = []

    def _get(url, **kw):
        calls.append(url)
        r = mock.Mock()
        r.json.return_value = responses[min(len(calls) - 1, len(responses) - 1)]
        return r
    return _get, calls


class TestNasdaq(unittest.TestCase):
    def test_num_strips_currency_and_thousands(self):
        # float("$1,234.56") raises, so this conversion is load-bearing, not cosmetic.
        self.assertEqual(nasdaq.num({"close": "$1,234.56"}, "close"), 1234.56)
        self.assertEqual(nasdaq.num({"volume": "37,881,200"}, "volume"), 37881200)

    def test_num_returns_none_for_na(self):
        # Live bug: float('N/A') killed a 414-symbol sweep from inside a worker thread.
        self.assertIsNone(nasdaq.num({"open": "N/A"}, "open"))

    def test_iso_reorders(self):
        self.assertEqual(nasdaq.iso("10/09/2026"), "2026-10-09")

    def test_returns_iso_keyed_bars(self):
        get, _ = fake_get([payload([row()])])
        with mock.patch.object(nasdaq.requests, "get", get):
            self.assertEqual(nasdaq.daily_bars("AAPL"),
                             {"2026-10-09": (10.0, 11.0, 9.0, 10.5, 1000.0)})

    def test_a_row_with_no_price_is_dropped(self):
        # Appending it would write a zero close into the price warehouse.
        get, _ = fake_get([payload([row(date="10/09/2026"),
                                    row(date="10/08/2026", close="N/A")])])
        with mock.patch.object(nasdaq.requests, "get", get):
            self.assertEqual(list(nasdaq.daily_bars("AAPL")), ["2026-10-09"])

    def test_a_missing_volume_is_a_real_zero(self):
        r = row()
        r["volume"] = "N/A"          # no trading that day: 0, not "unknown"
        get, _ = fake_get([payload([r])])
        with mock.patch.object(nasdaq.requests, "get", get):
            self.assertEqual(nasdaq.daily_bars("AAPL")["2026-10-09"][4], 0.0)

    def test_falls_back_to_the_etf_assetclass(self):
        # `stocks` answering with no rows is how an ETF is normally found, not an error.
        get, calls = fake_get([payload([]), payload([row()])])
        with mock.patch.object(nasdaq.requests, "get", get):
            got = nasdaq.daily_bars("SPY")
        self.assertIn("2026-10-09", got)
        self.assertIn("etf", calls[-1])

    def test_a_dashed_share_class_is_retried_with_a_dot(self):
        # BRK-B answers data:null on Nasdaq; BRK.B answers with bars.
        get, calls = fake_get([payload([]), payload([]), payload([row()]), payload([])])
        with mock.patch.object(nasdaq.requests, "get", get):
            self.assertIn("2026-10-09", nasdaq.daily_bars("BRK-B"))
        self.assertIn("BRK.B", calls[2])

    def test_an_empty_table_is_not_an_exception(self):
        # A future `todate` answers HTTP 200 with rows=[], which looks like a delisted symbol.
        get, _ = fake_get([payload([])])
        with mock.patch.object(nasdaq.requests, "get", get):
            self.assertEqual(nasdaq.daily_bars("NOPE"), {})

    def test_retries_then_gives_up_with_an_empty_dict(self):
        def boom(url, **kw):
            raise OSError("TLS reset")

        with mock.patch.object(nasdaq.requests, "get", boom):
            with mock.patch.object(nasdaq, "time"):   # else the retry backoff is real
                self.assertEqual(nasdaq.daily_bars("AAPL"), {})


if __name__ == "__main__":
    unittest.main()
