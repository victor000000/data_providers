"""Every fill gets real one-minute underlying bars, including non-tier1 stocks."""
import datetime as dt
import unittest
from unittest.mock import Mock,patch
import duckdb
from data_providers.jobs.data_ops.fill_underlying_minutes import mode_fill_underlyings
from data_providers.jobs.data_ops import download_minute_bars as app
from data_providers.providers import alpaca_http


class FillUnderlyingTests(unittest.TestCase):
    def setUp(self):
        self.con=duckdb.connect()
        for schema in ('tr','mh','mhro'):self.con.execute(f'CREATE SCHEMA {schema}')
        self.con.execute('CREATE TABLE tr.fills(underlying TEXT,ts_utc TIMESTAMP,broker TEXT)')
        self.con.execute("INSERT INTO tr.fills VALUES ('SPY','2026-10-15 14:00:00','Schwab'),('NXPI','2026-10-15 14:00:00','IBKR'),('A2701','2026-10-15 14:00:00','qsqh')")
        for schema in ('mh','mhro'):
            self.con.execute(f'''CREATE TABLE {schema}.underlying_minutes(ts_utc TIMESTAMP,symbol TEXT,
                open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,oi DOUBLE,source TEXT,PRIMARY KEY(ts_utc,symbol,source))''')
        self.con.execute("INSERT INTO mh.underlying_minutes VALUES ('2026-10-15 13:30:00','NXPI',100,101,99,100,1,NULL,'alpaca_1min')")
        self.con.execute("INSERT INTO mhro.underlying_minutes VALUES ('2026-10-15 13:30:00','SPY',100,101,99,100,1,NULL,'yfinance_1h')")
        self.bar={'t':'2026-10-15T13:30:00Z','o':100.,'h':101.,'l':99.,'c':100.,'v':5}

    def tearDown(self):self.con.close()

    def test_hourly_cache_does_not_replace_minutes_and_staged_minutes_are_reused(self):
        fetch=Mock(return_value=[self.bar])
        self.assertEqual(mode_fill_underlyings(self.con,fetch,Mock(),workers=1),0)
        fetch.assert_called_once_with('SPY',dt.date(2026,10,15),dt.date(2026,10,15))
        self.assertEqual(self.con.execute("SELECT count(*) FROM mh.underlying_minutes WHERE symbol='SPY' AND source='alpaca_1min'").fetchone(),(1,))
        self.assertEqual(self.con.execute('SELECT count(*) FROM mhro.underlying_minutes').fetchone(),(1,))

    def test_feed_failure_keeps_existing_data_and_reports_failure(self):
        self.assertEqual(mode_fill_underlyings(self.con,Mock(side_effect=RuntimeError('offline')),Mock(),workers=1),1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM mh.underlying_minutes').fetchone(),(1,))

    def test_wrong_day_response_does_not_write_partial_data(self):
        invalid={**self.bar,'t':'2026-10-16T13:30:00Z'}
        self.assertEqual(mode_fill_underlyings(self.con,Mock(return_value=[self.bar,invalid]),Mock(),workers=1),1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM mh.underlying_minutes').fetchone(),(1,))

    def test_fills_cli_always_runs_both_tapes_and_propagates_either_failure(self):
        con=Mock()
        with patch.object(app.db,'connect',return_value=con),patch.object(app,'mode_fills',return_value=0), \
             patch.object(app,'mode_fill_underlyings',return_value=1) as under,patch('sys.argv',['minute','--mode','fills']):
            self.assertEqual(app.main(),1)
        under.assert_called_once_with(con,app.fetch_range,app.status)
        con.close.assert_called_once()

    def test_stock_pagination_claims_one_rate_slot_per_http_request(self):
        response1=Mock(status_code=200,headers={});response1.json.return_value={'bars':{'SPY':[1]},'next_page_token':'next'}
        response2=Mock(status_code=200,headers={});response2.json.return_value={'bars':{'SPY':[2]}}
        session=Mock();session.get.side_effect=[response1,response2]
        with patch.object(app,'HDR',{'X-Test':'dummy'}),patch.object(alpaca_http._local,'session',session,create=True), \
             patch.object(alpaca_http,'throttle') as gate,patch.object(alpaca_http,'note_headers'):
            self.assertEqual(app.fetch_range('SPY',dt.date(2026,10,15),dt.date(2026,10,16)),[1,2])
        self.assertEqual(gate.call_count,2)
