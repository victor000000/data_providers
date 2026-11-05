from datetime import UTC, date, datetime

import duckdb
import pytest

from data_providers.jobs.history.futures_store import merge, open_stage, upsert
from data_providers.jobs.history.maintain_us_futures import bar_row, broker_symbol, universe


def test_legacy_futures_iv_is_namespaced_and_does_not_import_equities_or_indices(tmp_path):
    from data_providers.jobs.audit.us_futures_options import legacy_iv_rows
    day = date(2026, 10, 16)
    rows = legacy_iv_rows([('ES:CME', 'ibkr_tws30d', day, .2), ('ES', 'ibkr_tws30d', day, .8),
                           ('SPX:IDX', 'ibkr_tws30d', day, .3), ('ES:CME', 'other', day, .9)],
                          [('ES', 'CME')], 'input_release')
    assert len(rows) == 1 and rows[0][1:4] == ('CME', 'ES', 'CME:ES:ibkr_contfuture_legacy')
    assert rows[0][5] == .2 and rows[0][11:15] == (None, '[]', None, None)
    path, stage = open_stage(tmp_path, 'legacy-test')
    upsert(stage, 'us_futures_iv', rows)
    with pytest.raises(duckdb.ConstraintException):
        bad = list(rows[0]); bad[5] = float('inf')
        upsert(stage, 'us_futures_iv', [bad])
    with pytest.raises(duckdb.ConstraintException):
        bad = list(rows[0]); bad[3] = 'ES'
        upsert(stage, 'us_futures_iv', [bad])
    stage.close()
    assert merge(tmp_path, path)['verified']
    assert merge(tmp_path, path)['verified']
    with duckdb.connect(str(tmp_path / 'market_history.duckdb'), read_only=True) as c:
        assert c.execute('SELECT count(*),max(atm_iv) FROM us_futures_iv').fetchone() == (1, .2)
    with pytest.raises(ValueError, match='Invalid legacy'):
        legacy_iv_rows([('ES:CME', 'ibkr_tws30d', day, 0)], [('ES', 'CME')], 'r')


def test_catalog_progress_never_turns_discovery_into_optionability():
    import json
    from data_providers.jobs.audit.publish_futures_catalog import product_rows
    rows = product_rows([{'product_code': 'ES', 'trading_venue': 'XCME', 'date': '2026-10-16',
                          'name': 'E-mini S&P', 'type': 'single'},
                         {'product_code': 'X', 'date': '2026-10-16', 'name': 'Unknown', 'type': 'combo'}],
                        {'sha256': 'receipt'})
    assert rows[0][2] == 'CME' and rows[1][2] == 'UNSPECIFIED'
    first, unknown = [json.loads(row[-1])['_provider_coverage'] for row in rows]
    assert first['venue_scope'] == 'US_listed' and unknown['venue_scope'] == 'unknown'
    assert first['optionability'] == 'unknown_until_actual_option_inventory_evidence'
    assert not first['price_iv_history_complete']
    variants = product_rows([{'product_code': 'ES', 'trading_venue': 'XCME', 'date': '2026-10-16',
                              'name': 'Single', 'type': 'single'},
                             {'product_code': 'ES', 'trading_venue': 'XCME', 'date': '2026-10-16',
                              'name': 'Spread', 'type': 'combo'}], {'sha256': 'receipt'})
    assert len(variants) == 1
    details = json.loads(variants[0][-1])
    assert len(details['_definition_variants']) == 2
    assert 'type' not in details and 'type' in details['_provider_coverage']['conflicting_metadata_fields']


def test_session_day_is_not_utc_start_day():
    row=bar_row('ES','CME','ESZ6',{'session_end_date':'2026-10-16',
        'window_start':1792022400000000000,'open':7800,'high':7810,'low':7700,
        'close':7703,'settlement_price':7704.25,'volume':2286825})
    assert row[4]==date(2026,10,16)
    assert row[9]==7704.25 and row[8]==7703
    assert row[-1].tzinfo==UTC
    with pytest.raises(ValueError):
        bar_row('ES','CME','ESZ6',{'session_end_date':'2026-10-16','window_start':0,
                                'open':1,'high':1,'low':3,'close':2})


def test_universe_deduplicates_and_keeps_futures_separate(tmp_path):
    p=tmp_path/'universe.txt';p.write_text('CL\nIDX:NQ:CME\nFUT:CL:NYMEX\nFUT:CL:NYMEX\nFUT:6E:CME\n')
    assert universe(p)==[('CL','NYMEX'),('6E','CME')]
    assert broker_symbol('6E')=='EUR' and broker_symbol('6J')=='JPY'


def test_staging_merge_exact_idempotence_and_corrections(tmp_path):
    path,con=open_stage(tmp_path,'test')
    row=bar_row('CL','NYMEX','CLX6',{'session_end_date':'2026-10-16','window_start':1792022400000000000,
        'open':70,'high':72,'low':69,'close':71,'volume':100,'settlement_price':71.2})
    upsert(con,'us_futures_daily',[row]);con.close()
    assert merge(tmp_path,path)['verified']
    assert merge(tmp_path,path)['retained']
    with duckdb.connect(str(tmp_path/'market_history.duckdb'),read_only=True) as target:
        assert target.execute('SELECT count(*),max(close) FROM us_futures_daily').fetchone()==(1,71)
    _,con=open_stage(tmp_path,'test')
    revised=list(row);revised[8]=71.5
    upsert(con,'us_futures_daily',[revised]);con.close()
    merge(tmp_path,path)
    with duckdb.connect(str(tmp_path/'market_history.duckdb'),read_only=True) as target:
        assert target.execute('SELECT count(*),max(close) FROM us_futures_daily').fetchone()==(1,71.5)


def test_cn_all_strikes_snapshot_preserves_identity_and_unknowns():
    from data_providers.jobs.history.maintain_cn_futures import quotes
    stamp=datetime(2026,10,17,8,tzinfo=UTC)
    data={'up':[[1,2,3,4,5,6,7,8000,'ru2701C8000'],[1,2,'--',4,5,6,7,8100,'ru2701C8100']],
          'down':[[1,2,3,4,5,6,7,'ru2701P8000']]}
    rows=quotes(data,'SHFE','RU','ru2701',stamp)
    assert len(rows)==3 and rows[0][3]=='ru2701C8000'
    assert rows[1][9] is None and rows[2][7]=='P'
    assert all(row[5]=='FUTURES_OPTION' and row[8]==stamp for row in rows)
    data['up'][0][-1]='ru2701P8000'
    with pytest.raises(ValueError,match='identity'):
        quotes(data,'SHFE','RU','ru2701',stamp)


def test_cn_contract_daily_uses_deliverable_not_continuous():
    from data_providers.jobs.history.maintain_cn_futures import daily_rows
    rows=daily_rows([{'d':'2026-10-16','o':'100','h':'110','l':'90','c':'105','v':'50','p':'60','s':'104'}],
                    'SHFE','RU','ru2701')
    assert rows[0][:4]==('SHFE','RU','RU2701',date(2026,10,16))
    assert rows[0][8:]==(104,50,60,'sina_contract')


def test_etf_universe_covers_all_source_rosters(tmp_path):
    from data_providers.jobs.history.build_etf_universe import build
    folder=tmp_path/'market_data';folder.mkdir()
    with duckdb.connect(str(folder/'yahoo.duckdb')) as c:
        c.execute('CREATE TABLE index_membership(symbol VARCHAR,index_slug VARCHAR,status VARCHAR)')
        c.execute("INSERT INTO index_membership VALUES ('SPY','etf500','Current'),('QQQ','etf311','Current'),('SPY','etf311','Current'),('OLD','etf500','Past'),('AAPL','sp500','Current')")
    path,symbols=build(tmp_path)
    assert {'QQQ','SPY','VXX','VIXY','VIXM','VXZ','UVXY','SVXY','UVIX','SVIX'}==set(symbols)
    assert 'VXY' not in symbols
    assert path.read_text().count('SPY')==1


def test_default_plan_includes_us_cn_derivatives_and_etfs():
    from data_providers.maintenance import plan
    modules=[s.module for s in plan('all',with_bars=True)]
    assert 'data_providers.jobs.history.maintain_us_futures' in modules
    assert 'data_providers.jobs.history.maintain_cn_futures' in modules
    assert 'data_providers.jobs.history.build_etf_universe' in modules
    assert any(s.args and s.args[-1]=='etf_bars' for s in plan('all',with_bars=True))


def test_broker_and_price_contract_identity_has_expiry():
    from ib_async import ContractDetails, Future

    from data_providers.jobs.history.maintain_us_futures import contract_key, contract_row
    c=Future(conId=123,symbol='ES',localSymbol='ESZ6',lastTradeDateOrContractMonth='20261218',exchange='CME',currency='USD')
    key=contract_key(c,'CME')
    assert key=='CME:ESZ6:2026-12-18'
    metadata=contract_row('ES','CME',ContractDetails(contract=c),datetime.now(UTC))
    row=bar_row('ES','CME','ESZ6',{'session_end_date':'2026-10-16','window_start':1792022400000000000,
        'open':1,'high':3,'low':1,'close':2},key)
    assert row[1]==metadata[1]


def test_sina_names_are_provider_specific_and_quotes_cannot_change_product():
    from data_providers.jobs.history.maintain_cn_futures import quotes
    from data_providers.providers.sina_futures import source_name
    assert source_name('MA','MA')=='甲醇期权'
    assert source_name('RU','天胶期权')=='橡胶期权'
    with pytest.raises(ValueError):
        quotes({'up':[[1,2,3,4,5,6,7,8000,'ru2701C8000']]},'DCE','PP','pp2701',datetime.now(UTC))


def test_full_cn_option_history_never_fabricates_expiry_or_greeks():
    from data_providers.jobs.history.maintain_cn_option_history import option_rows
    rows=option_rows([[10,12,8,11,50,'2026-10-16']], 'DCE','M','m2701C3000',None,date(2026,10,16))
    assert len(rows)==1 and rows[0][3] is None
    assert rows[0][4:7]==(3000,'C',date(2026,10,16))
    assert rows[0][-4:]==(None,None,None,None)
    with pytest.raises(ValueError,match='follows expiry'):
        option_rows([[10,12,8,11,50,'2026-10-16']], 'DCE','M','m2701C3000',date(2026,10,15),date(2026,10,16))


def test_fop_entitlement_errors_are_not_empty_history():
    import asyncio

    from data_providers.jobs.history.maintain_us_futures import historical_options
    class Event:
        def __init__(self):self.handlers=[]
        def __iadd__(self,f):self.handlers.append(f);return self
        def __isub__(self,f):self.handlers.remove(f);return self
    class Broker:
        errorEvent=Event()
        async def reqHistoricalDataAsync(self,*args,**kwargs):
            for handler in self.errorEvent.handlers:handler(1,10089,'Subscription required')
            return []
    broker=Broker()
    with pytest.raises(RuntimeError,match='Subscription'):
        asyncio.run(historical_options(broker,object(),'5 D'))
    assert not broker.errorEvent.handlers


def test_cn_vendor_cannot_substitute_another_contract_month():
    from data_providers.jobs.history.maintain_cn_futures import quotes
    with pytest.raises(ValueError,match='different contract month'):
        quotes({'up':[[1,2,3,4,5,6,7,8000,'ru2701C8000']]},'SHFE','RU','ru2501',datetime.now(UTC))
    assert quotes({'up':[[1,2,3,4,5,6,7,8000,'MA701C8000']]},'CZCE','MA','ma2701',datetime.now(UTC))


def test_cfe_option_identity_is_futures_option_and_strike_scaled():
    from data_providers.providers.cboe_futures import future_identity, option_identity
    assert future_identity({'product_display':'VX+VXT43/V6','expire_date':'2026-10-28'}) == (
        'CFE:VX43V6:2026-10-28','VX43V6',date(2026,10,28))
    assert option_identity({'id':'abcdef','futures_root':'UX','product_display':'UX1A/V6 C1500',
                            'expire_date':'2026-10-05'}) == ('CFE:UX:abcdef',date(2026,10,5),15,'C')
    with pytest.raises(ValueError):
        option_identity({'id':'x','futures_root':'VIX','product_display':'VIX C1500'})


def test_cfe_nontraded_ranges_are_not_executed_ohlc():
    from data_providers.providers.cboe_futures import values
    row={'Open':'0','High':'21','Low':'22','Close':'0','Settle':'21.50','Total Volume':'0','Open Interest':'12'}
    assert values(row)==([None]*4,21.5,0,12)
    row.update(Open='20',High='22',Low='19',Close='21',**{'Total Volume':'4'})
    assert values(row)==([20,22,19,21],21.5,4,12)
    row['High']='18'
    with pytest.raises(ValueError,match='OHLC'):values(row)


def test_new_catalog_schema_accepts_retained_older_stage(tmp_path):
    path,con=open_stage(tmp_path,'old')
    con.execute('DROP TABLE us_futures_products')
    con.execute('DROP TABLE us_futures_option_settlements')
    con.close()
    assert merge(tmp_path,path)['verified']
    with duckdb.connect(str(tmp_path/'market_history.duckdb')) as reader:
        assert reader.execute('SELECT count(*) FROM us_futures_option_settlements').fetchone()==(0,)


def test_provider_catalog_identity_keeps_venue_and_full_expiry():
    from data_providers.jobs.history.maintain_catalogs import reference_row
    row=reference_row({'ticker':'ESZ6','last_trade_date':'2026-12-18','trading_venue':'XCME','product_code':'ES'},datetime.now(UTC))
    assert row[1:5]==('CME:ESZ6:2026-12-18','ES','CME','FUT')
    from data_providers.jobs.history.maintain_cn_catalog import root_and_venue
    assert root_and_venue('sc2611','SHFE')==('SC','INE')
    assert root_and_venue('IF2612','CFFEX')==('IF','CFFEX')
    with pytest.raises(ValueError):root_and_venue('IF0','CFFEX')


def test_cfe_roster_is_discovered_from_exchange_payload():
    import json

    from data_providers.jobs.history.maintain_cfe_catalog import products
    roster=[{'label':'Mini VIX Futures','value':'VXM'},{'label':'VIX Futures','value':'VX'}]
    payload=json.dumps([1,'3a:'+json.dumps({'products':roster})])
    assert products('<script>self.__next_f.push('+payload+')</script>')==roster
    with pytest.raises(ValueError):products('<html>service unavailable</html>')
