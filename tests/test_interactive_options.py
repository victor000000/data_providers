import unittest
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from urllib.error import HTTPError
from data_providers.providers.option_quote import quote,contract
from data_providers.providers.us_option_data import read_snapshots
from data_providers.providers.cn_live_chain import chain


class InteractiveQuotesTests(unittest.TestCase):
    def test_sentinels_crossed_and_old_quotes(self):
        self.assertIsNone(quote('x',-1,float('nan'),source='IBKR',feed='live')['midpoint'])
        self.assertEqual(quote('x',2,1,source='x',feed='opra')['state'],'crossed')
        stamp=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
        q=quote('x',1,2,source='x',feed='opra',quote_time=stamp)
        self.assertEqual(q['state'],'stale');self.assertFalse(q['executable_reference'])

    def test_indicative_is_never_executable_reference(self):
        q=quote('x',1,2,source='Alpaca',feed='indicative',quote_time=datetime.now(timezone.utc).isoformat())
        self.assertEqual(q['state'],'indicative');self.assertFalse(q['executable_reference'])
        self.assertFalse(quote('x',1,2,source='IBKR',feed='live')['executable_reference'])

    def test_opra_entitlement_fallback_is_explicit(self):
        error=HTTPError('https://data.alpaca.markets',403,'Denied',{},None)
        snap={'snapshots':{'SPY991218P00550000':{'latestQuote':{'bp':1,'ap':2}}}}
        with patch('data_providers.providers.us_option_data.request',side_effect=[error,snap]):
            result=read_snapshots({'symbols':'SPY991218P00550000'})
        self.assertEqual(result['feed'],'indicative');self.assertTrue(result['warning'])

    def test_cn_does_not_guess_missing_contract_month(self):
        with patch('data_providers.providers.cn_live_chain.stored_chain',return_value=[]):
            with self.assertRaises(ValueError):chain('CZCE','MA','MA2701')
        with self.assertRaises(ValueError):chain('HK','MA','MA2701')
