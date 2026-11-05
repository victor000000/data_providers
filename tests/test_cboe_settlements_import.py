import hashlib
import json
import duckdb
import pytest
from data_providers import MarketData
from data_providers.jobs.history.import_cboe_settlements import run


def fixture(tmp_path):
    duckdb.connect(str(tmp_path/'market_history.duckdb')).close()
    rawrow=dict(trading_symbol='SPXW',settlement_symbol='SPX',expiration_date='2025-04-07',settlement_value=5062.25)
    raw=tmp_path/'raw.json';raw.write_text(json.dumps({'data':[rawrow]}))
    sha=hashlib.sha256(raw.read_bytes()).hexdigest()
    url='https://www.cboe.com/index_settlement_values/get_sv_data/W/2025/'
    received='2026-10-25T23:12:00+00:00'
    source=dict(path=str(raw),sha256=sha,url=url,http_status=200,received_at=received)
    row=dict(rawrow,source='cboe_official_weekly_settlement',source_sha256=sha,source_url=url,received_at=received,published_at=None,available_at=None)
    delivery=tmp_path/'delivery.json';delivery.write_text(json.dumps({'sources':[source],'rows':[row]}))
    return delivery,raw


def test_exact_value_null_clocks_idempotency_and_conflict(tmp_path):
    delivery,_=fixture(tmp_path)
    assert run(delivery,home=tmp_path)['new_canonical_rows']==1
    assert run(delivery,home=tmp_path)['new_canonical_rows']==0
    rows=MarketData(tmp_path).read('us_index_option_settlements',symbols=['SPX'],start='2025-04-07',end='2025-04-07')
    assert len(rows)==1 and float(rows[0]['settlement_value'])==5062.25
    assert rows[0]['available_at'] is None and rows[0]['published_at'] is None
    with duckdb.connect(str(tmp_path/'market_history.duckdb')) as c:
        c.execute('UPDATE index_option_settlements SET settlement_value=1')
    with pytest.raises(duckdb.ConstraintException):run(delivery,home=tmp_path)
    assert float(MarketData(tmp_path).read('us_index_option_settlements')[0]['settlement_value'])==1


@pytest.mark.parametrize('change',['raw','value','clock','duplicate'])
def test_tampered_evidence_rejected(tmp_path,change):
    delivery,raw=fixture(tmp_path)
    data=json.loads(delivery.read_text())
    if change=='raw':raw.write_text('{}')
    if change=='value':data['rows'][0]['settlement_value']=9
    if change=='clock':data['rows'][0]['available_at']='2025-04-07T20:00:00Z'
    if change=='duplicate':data['rows']*=2
    delivery.write_text(json.dumps(data))
    with pytest.raises(ValueError):run(delivery,home=tmp_path)
    assert not (tmp_path/'staging').exists()


@pytest.mark.parametrize('label', ['cboe_official_eom_settlement', 'cboe_official_weekly_settlement'])
def test_eom_source_identity_is_separate(tmp_path, label):
    delivery, _ = fixture(tmp_path)
    data = json.loads(delivery.read_text())
    url = data['sources'][0]['url'].replace('/W/', '/M/')
    data['sources'][0]['url'] = url
    data['rows'][0].update(source_url=url, source=label)
    delivery.write_text(json.dumps(data))
    if label == 'cboe_official_weekly_settlement':
        with pytest.raises(ValueError):
            run(delivery, home=tmp_path)
    else:
        assert run(delivery, home=tmp_path)['new_canonical_rows'] == 1
        assert run(delivery, home=tmp_path)['new_canonical_rows'] == 0
        row = MarketData(tmp_path).read('us_index_option_settlements')[0]
        assert row['source'] == label and row['available_at'] is None


@pytest.mark.parametrize('trading,settlement', [('SPXW','SPX'), ('SPX','SET')])
def test_standard_category_only_accepts_spxw_pm(tmp_path, trading, settlement):
    delivery, raw = fixture(tmp_path)
    data = json.loads(delivery.read_text())
    original = json.loads(raw.read_text())
    original['data'][0].update(trading_symbol=trading, settlement_symbol=settlement)
    raw.write_text(json.dumps(original))
    sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    url = data['sources'][0]['url'].replace('/W/', '/S/')
    data['sources'][0].update(url=url, sha256=sha)
    data['rows'][0].update(trading_symbol=trading, settlement_symbol=settlement,
                          source='cboe_official_standard_pm_settlement', source_url=url, source_sha256=sha)
    delivery.write_text(json.dumps(data))
    if trading == 'SPX':
        with pytest.raises(ValueError):
            run(delivery, home=tmp_path)
    else:
        assert run(delivery, home=tmp_path)['new_canonical_rows'] == 1
        assert run(delivery, home=tmp_path)['new_canonical_rows'] == 0
