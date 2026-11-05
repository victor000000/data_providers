from datetime import date, datetime, UTC
import duckdb
from data_providers import MarketData
from data_providers.data.schema.fx import ensure


def test_fx_source_versions_and_unknown_clocks(tmp_path):
    with duckdb.connect(str(tmp_path/'market_history.duckdb')) as c:
        ensure(c)
        for sha,rate in [('one',6.7),('two',6.8),('two',6.8)]:
            c.execute('INSERT INTO fx_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                      ['yahoo_CNY=X','USD','CNY',date(2026,10,23),rate,'daily_close',None,None,None,datetime.now(UTC),sha,'{}'])
    rows=MarketData(home=tmp_path,backend='duckdb').read('fx_observations',symbols=['USD'],start='2026-10-23',end='2026-10-23')
    assert len(rows)==2
    assert all(r['event_ts'] is None and r['available_at'] is None and r['captured_at'] is None for r in rows)
    assert {r['rate'] for r in rows}=={6.7,6.8}
    assert all(r['quote_currency']=='CNY' for r in rows)
