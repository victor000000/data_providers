"""The legacy full-bars entrypoint shares the working per-attempt transport."""
import unittest
from unittest.mock import Mock, patch
from data_providers.jobs.history import download_option_bars as app
from data_providers.providers import alpaca_http


class FullBarsTransportTests(unittest.TestCase):
    def test_retry_claims_one_slot_per_http_attempt(self):
        bad = Mock(status_code=503, headers={})
        good = Mock(status_code=200, headers={})
        good.json.return_value = {'bars': {}}
        session = Mock()
        session.get.side_effect = [bad, good]
        with patch.object(app, 'HDR', {'X-Test': 'dummy'}), \
             patch.object(alpaca_http._local, 'session', session, create=True), \
             patch.object(alpaca_http, 'throttle') as gate, \
             patch.object(alpaca_http, 'note_headers'), \
             patch.object(alpaca_http.time, 'sleep'):
            self.assertEqual(app.get('https://data.alpaca.markets/test'), {'bars': {}})
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(gate.call_count, 2)
        self.assertEqual(session.get.call_args.kwargs['headers'], {'X-Test': 'dummy'})

    def test_auth_failure_is_not_retried(self):
        response = Mock(status_code=403, headers={})
        response.raise_for_status.side_effect = RuntimeError('forbidden')
        session = Mock()
        session.get.return_value = response
        with patch.object(app, 'HDR', {'X-Test': 'dummy'}), \
             patch.object(alpaca_http._local, 'session', session, create=True), \
             patch.object(alpaca_http, 'throttle'), \
             patch.object(alpaca_http, 'note_headers'):
            with self.assertRaisesRegex(RuntimeError, 'forbidden'):
                app.get('https://data.alpaca.markets/test')
        session.get.assert_called_once()
