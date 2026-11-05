"""Failed exchange candidates cannot silently become a successful empty IV history."""
import datetime as dt
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from data_providers.jobs.history import fetch_iv_history_ibkr as app


class IVHistoryFetchTests(unittest.TestCase):
    def test_all_request_errors_are_raised(self):
        with patch.object(app, 'historical_iv_bars', side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError):
                app.fetch_iv(None, 'SPX', ['first', 'second'], '2 Y')

    def test_next_exchange_can_recover_and_invalid_iv_is_filtered(self):
        bars = [SimpleNamespace(date=dt.date(2026,10,16), close=v) for v in [.2,0,5,11021]]
        with patch.object(app, 'historical_iv_bars', side_effect=[TimeoutError(), bars]):
            self.assertEqual(app.fetch_iv(None, 'SPX', ['first','second'], '2 Y'),
                             [('2026-10-16', .2)])

    def test_confirmed_empty_remains_empty(self):
        with patch.object(app, 'historical_iv_bars', return_value=[]):
            self.assertEqual(app.fetch_iv(None, 'SPX', ['first'], '2 Y'), [])


class IVInstrumentNamespaceTests(unittest.TestCase):
    def test_stock_and_same_named_future_have_distinct_storage_keys(self):
        stock,stocks=app.parse_line('CL')
        future,futures=app.parse_line('FUT:CL:NYMEX')
        self.assertEqual(stock,'CL');self.assertEqual(future,'CL:NYMEX')
        self.assertEqual(stocks[0].secType,'STK')
        self.assertEqual(futures[0].secType,'CONTFUT')
        self.assertEqual(futures[0].exchange,'NYMEX')

    def test_index_storage_key_preserves_all_exchange_candidates(self):
        label,contracts=app.parse_line('IDX:RUT:RUSSELL,CBOE,SMART')
        self.assertEqual(label,'RUT:IDX')
        self.assertEqual([c.exchange for c in contracts],['RUSSELL','CBOE','SMART'])
        self.assertTrue(all(c.secType=='IND' for c in contracts))

    def test_class_share_equity_key_remains_compatible_with_other_price_sources(self):
        label,contracts=app.parse_line('BRK-B')
        self.assertEqual(label,'BRK-B');self.assertEqual(contracts[0].symbol,'BRK B')
