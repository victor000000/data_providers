"""Yahoo pacing covers HTTP calls, including retries and cookie requests."""
import unittest
from unittest.mock import patch
from curl_cffi import requests
from data_providers.providers import yahoo_session as app


class YahooPacingTests(unittest.TestCase):
    def test_get_and_post_claim_the_shared_gate(self):
        session = app.create_session()
        try:
            for method in ('get', 'post'):
                with self.subTest(method=method), patch.object(app, 'throttle') as gate, patch.object(requests.Session, method, return_value='response') as send:
                    self.assertEqual(getattr(session, method)('https://query1.finance.yahoo.com/test', timeout=5), 'response')
                    gate.assert_called_once_with('yfinance')
                    send.assert_called_once_with('https://query1.finance.yahoo.com/test', timeout=5)
        finally:
            session.close()
