"""Transport recovery must respect Alpaca pacing and distinguish permanent denials."""
import unittest
from unittest.mock import Mock,patch
from data_providers.providers import alpaca_http as app


def response(code):
    r=Mock(status_code=code,headers={})
    r.json.return_value={'bars':{}}
    if code>=400:
        r.raise_for_status.side_effect=app.requests.exceptions.HTTPError(f'HTTP {code}')
    return r


class AlpacaHTTPTests(unittest.TestCase):
    def run_get(self, effects, tries=3):
        session=Mock();session.get.side_effect=effects
        return session, patch.object(app._local,'session',session,create=True)

    def test_success_uses_headers_and_accounts_for_request(self):
        session,session_patch=self.run_get([response(200)])
        with session_patch, patch.object(app,'throttle') as gate, patch.object(app,'note_headers') as note:
            self.assertEqual(app.get_json('https://data.alpaca.markets/test',{'auth':'test'}),{'bars':{}})
            session.get.assert_called_once_with('https://data.alpaca.markets/test',headers={'auth':'test'},timeout=30)
            gate.assert_called_once_with('alpaca');note.assert_called_once()

    def test_transport_retry_claims_a_slot_for_every_attempt(self):
        failure=app.requests.exceptions.RequestException('TLS closed')
        session,session_patch=self.run_get([failure,response(200)])
        with session_patch,patch.object(app,'throttle') as gate,patch.object(app,'note_headers'),patch.object(app.time,'sleep'):
            self.assertEqual(app.get_json('https://data.alpaca.markets/test',{}),{'bars':{}})
            self.assertEqual(gate.call_count,2)
            self.assertEqual(session.get.call_count,2)

    def test_entitlement_denial_is_not_retried(self):
        session,session_patch=self.run_get([response(403)])
        with session_patch,patch.object(app,'throttle'),patch.object(app,'note_headers'),patch.object(app.time,'sleep') as pause:
            with self.assertRaises(app.requests.exceptions.HTTPError):
                app.get_json('https://data.alpaca.markets/test',{})
            self.assertEqual(session.get.call_count,1);pause.assert_not_called()

    def test_rate_limit_and_server_failures_are_bounded(self):
        session,session_patch=self.run_get([response(429),response(503),response(503)])
        with session_patch,patch.object(app,'throttle') as gate,patch.object(app,'note_headers'),patch.object(app.time,'sleep'):
            with self.assertRaises(app.requests.exceptions.HTTPError):
                app.get_json('https://data.alpaca.markets/test',{},tries=3)
            self.assertEqual(gate.call_count,3)

    def test_exhausted_transport_failure_is_raised(self):
        session,session_patch=self.run_get([app.requests.exceptions.RequestException('TLS closed')]*2)
        with session_patch,patch.object(app,'throttle') as gate,patch.object(app.time,'sleep'):
            with self.assertRaises(app.requests.exceptions.RequestException):
                app.get_json('https://data.alpaca.markets/test',{},tries=2)
            self.assertEqual(gate.call_count,2)
