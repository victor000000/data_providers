"""Persistent terminal settings and stock symbology must survive a new client process."""
import os
import unittest
from unittest.mock import patch
from data_providers.providers import thetadata as app
from data_providers.data.symbols import SymbolError


class ThetaConfigTests(unittest.TestCase):
    def test_loopback_bypasses_proxy_environment_without_no_proxy(self):
        env={'HTTP_PROXY':'http://proxy.invalid:7897','HTTPS_PROXY':'http://proxy.invalid:7897',
             'ALL_PROXY':'http://proxy.invalid:7897'}
        with patch.dict(os.environ,env,clear=True):
            for url in ['http://127.0.0.1:25503/v3','http://localhost:25503/v3','http://[::1]:25503/v3']:
                client=app.ThetaDataClient('test',base_url=url)
                settings=client.session.merge_environment_settings(url,{},None,None,None)
                self.assertFalse(client.session.trust_env)
                self.assertEqual(settings['proxies'],{})
                self.assertTrue(settings['verify'])

    def test_loopback_transport_works_with_unreachable_proxy_environment(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload=b'created,close\n1970-01-01T00:00:00,1\n'
                self.send_response(200)
                self.send_header('Content-Length',str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self,*args):
                pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ,{'HTTP_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1'},clear=True):
                client=app.ThetaDataClient('test',base_url=f'http://127.0.0.1:{server.server_port}/v3')
                with patch.object(client,'_rate_limit'):
                    rows=client._request_csv('stock/history/eod',{'symbol':'SYNTHETIC_TEST'})
                self.assertEqual(rows[0]['close'],'1')
                client.session.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_remote_endpoint_retains_proxy_environment(self):
        with patch.dict(os.environ,{'HTTP_PROXY':'http://proxy.invalid:7897'},clear=True):
            client=app.ThetaDataClient('test',base_url='http://terminal.example.test/v3')
            self.assertTrue(client.session.trust_env)
            settings=client.session.merge_environment_settings(client.base_url,{},None,None,None)
            self.assertEqual(settings['proxies']['http'],'http://proxy.invalid:7897')

    def test_localhost_lookalikes_are_not_treated_as_loopback(self):
        self.assertFalse(app._loopback_endpoint('http://localhost.example.test/v3'))
        self.assertFalse(app._loopback_endpoint('http://192.168.1.1/v3'))

    def test_connection_pool_is_bounded_and_retries_are_disabled(self):
        client=app.ThetaDataClient('test',base_url='http://127.0.0.1:25503/v3')
        adapter=client.session.get_adapter(client.base_url)
        self.assertEqual(adapter._pool_maxsize,4)
        self.assertTrue(adapter._pool_block)
        self.assertEqual(adapter.max_retries.total,0)

    def test_pacing_uses_monotonic_clock_and_retains_conservative_spacing(self):
        client=app.ThetaDataClient('test',base_url='http://127.0.0.1:25503/v3')
        with patch.object(app.time,'monotonic',side_effect=[100,100,100.1,100.5]),patch.object(app.time,'time',side_effect=AssertionError('wall clock used')),patch.object(app.time,'sleep') as sleep:
            client._rate_limit()
            client._rate_limit()
        self.assertAlmostEqual(sleep.call_args.args[0],.4)

    def test_endpoint_precedence(self):
        with patch.object(app,'_settings',return_value={'base_url':'http://configured/v3'}),patch.dict(os.environ,{},clear=True):
            self.assertEqual(app.ThetaDataClient('test').base_url,'http://configured/v3')
            with patch.dict(os.environ,{'THETADATA_BASE_URL':'http://env/v3/'}):
                self.assertEqual(app.ThetaDataClient('test').base_url,'http://env/v3')
                self.assertEqual(app.ThetaDataClient('test',base_url='http://explicit/v3/').base_url,'http://explicit/v3')

    def test_class_share_request_uses_provider_symbol_but_returns_requested_key(self):
        client=app.ThetaDataClient('test',base_url='http://localhost/v3')
        rows=[{'created':'2026-10-16T17:15:00','symbol':'BRK.B','close':'500','volume':'10'}]
        with patch('data_providers.data.symbols.canonical_for',return_value='BRK B'),patch('data_providers.data.symbols.for_source',return_value='BRK.B'),patch.object(client,'_request_csv',return_value=rows) as request:
            result=client.get_stock_eod('BRK-B','20261005','20261016')
        self.assertEqual(request.call_args.args[1]['symbol'],'BRK.B')
        self.assertEqual(result[0]['symbol'],'BRK-B')
        self.assertEqual(result[0]['date'],'2026-10-16')
        self.assertEqual(result[0]['close'],500.)

    def test_unmapped_explicit_symbol_is_retained(self):
        client=app.ThetaDataClient('test',base_url='http://localhost/v3')
        with patch('data_providers.data.symbols.canonical_for',return_value=None),patch('data_providers.data.symbols.for_source',side_effect=SymbolError()),patch.object(client,'_request_csv',return_value=[]) as request:
            self.assertEqual(client.get_stock_eod('DIRECT','20261005','20261016'),[])
        self.assertEqual(request.call_args.args[1]['symbol'],'DIRECT')


    def test_csv_request_keeps_terminal_key_out_of_url_parameters(self):
        from unittest.mock import Mock
        client=app.ThetaDataClient('private-test-key',base_url='http://localhost/v3')
        response=Mock(status_code=200,text='created,close\n2026-10-16T17:15:00,500\n')
        params={'symbol':'AAPL'}
        with patch.object(client,'_rate_limit'),patch.object(client.session,'get',return_value=response) as request:
            rows=client._request_csv('stock/history/eod',params)
        self.assertEqual(params,{'symbol':'AAPL'})
        self.assertNotIn('token',request.call_args.kwargs['params'])
        self.assertNotIn('private-test-key',str(request.call_args))
        self.assertEqual(rows[0]['close'],'500')
