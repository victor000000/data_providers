import hashlib
import json
from datetime import datetime, timezone
import duckdb
import pandas as pd
import pytest
from data_providers import MarketData
from data_providers.jobs.history.import_theta_intraday_greeks import run
from data_providers.lake import literal


def fixture_manifest(tmp_path):
    duckdb.connect(str(tmp_path/'market_history.duckdb')).close()
    rows=[]
    for minute in (30,35):
        rows.append(dict(source='thetadata_v3',contract_id='US:OPRA:SPXW:2026-07-13:P:7000',
            underlying='SPX',session_date='2026-07-13',interval_ts=datetime(2026,7,13,13,minute,tzinfo=timezone.utc),
            frequency_seconds=300,model_version='1',quote_ts=None,available_at=None,
            received_at=datetime(2026,10,25,tzinfo=timezone.utc),underlying_ts=datetime(2026,7,13,13,minute,tzinfo=timezone.utc),
            underlying_price=0.0 if minute==30 else 7000.0,iv=0.0 if minute==30 else .3,delta=-.2,
            quality='invalid_iv_spot_or_delta' if minute==30 else 'positive_iv_spot;model_units_and_availability_unverified',
            receipt_sha256='raw-hash',raw_json=json.dumps(dict(bid='2',ask='3',theta='-1',vega='2',rho='3',iv_error='0'))))
    parquet=tmp_path/'input.parquet'
    with duckdb.connect() as c:
        c.register('rows',pd.DataFrame(rows));c.execute('COPY rows TO '+literal(parquet)+' (FORMAT PARQUET)')
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'reports':[dict(path=str(parquet),rows=2,sha256=hashlib.sha256(parquet.read_bytes()).hexdigest())]}))
    return manifest,parquet


def test_preserves_intraday_rows_invalid_flags_and_unknown_clocks(tmp_path):
    manifest,_=fixture_manifest(tmp_path)
    result=run(manifest,home=tmp_path)
    assert result['new_canonical_rows']==2
    assert run(manifest,home=tmp_path)['new_canonical_rows']==0
    rows=MarketData(tmp_path).read('us_option_intraday_greeks',symbols=['SPX'],start='2026-07-13',end='2026-07-13')
    assert len(rows)==2
    assert all(r['available_at'] is None and r['quote_ts'] is None and r['gamma'] is None for r in rows)
    assert sum(r['quality']=='invalid_iv_spot_or_delta' for r in rows)==1
    assert all(r['theta']==-1 and r['vega']==2 for r in rows)
    with duckdb.connect(str(tmp_path/'market_history.duckdb')) as c:
        c.execute('UPDATE option_intraday_greeks SET delta=0.9')
    with pytest.raises(ValueError,match='Conflicting canonical'):
        run(manifest,home=tmp_path)
    assert all(r['delta']==.9 for r in MarketData(tmp_path).read('us_option_intraday_greeks'))


def test_changed_retained_parquet_is_not_ingested(tmp_path):
    manifest,parquet=fixture_manifest(tmp_path)
    parquet.write_bytes(parquet.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='checksum'):
        run(manifest,home=tmp_path)
    assert not (tmp_path/'staging').exists()
