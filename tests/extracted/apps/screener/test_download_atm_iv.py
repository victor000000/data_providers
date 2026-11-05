"""Daily option windows include the last session and retain explicit timestamp bounds."""
from urllib.parse import urlparse,parse_qs
from unittest.mock import patch
import unittest
from data_providers.jobs.history import download_atm_iv as app


class OptionBarWindowTests(unittest.TestCase):
    def test_date_bounds_include_the_whole_last_session(self):
        with patch.object(app,'get',return_value={'bars':{}}) as get:
            app.fetch(['SPY261120P00500000'],'2026-10-15','2026-10-16')
        args=parse_qs(urlparse(get.call_args.args[0]).query)
        self.assertEqual(args['start'],['2026-10-15T00:00:00Z'])
        self.assertEqual(args['end'],['2026-10-16T23:59:59Z'])

    def test_explicit_bound_and_pagination_are_preserved(self):
        pages=[{'bars':{'SPY':[1]},'next_page_token':'next'}, {'bars':{'SPY':[2]}}]
        with patch.object(app,'get',side_effect=pages) as get:
            result=app.fetch(['SPY'],'2026-10-15T13:30:00Z','2026-10-16T19:00:00Z')
        args=parse_qs(urlparse(get.call_args.args[0]).query)
        self.assertEqual(args['end'],['2026-10-16T19:00:00Z'])
        self.assertEqual(args['page_token'],['next'])
        self.assertEqual(result,{'SPY':[1,2]})

    def test_each_page_is_paced_once_by_the_transport(self):
        from data_providers.providers import alpaca_http
        from unittest.mock import Mock
        response=Mock(status_code=200,headers={})
        response.json.return_value={'bars':{}}
        session=Mock();session.get.return_value=response
        with patch.object(alpaca_http._local,'session',session,create=True),patch.object(alpaca_http,'throttle') as gate,patch.object(alpaca_http,'note_headers'):
            self.assertEqual(app.fetch(['SPY'],'2026-10-15','2026-10-16'),{})
        gate.assert_called_once_with('alpaca')
