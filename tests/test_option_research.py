from datetime import date
from pathlib import Path

import duckdb
import pytest

from data_providers import MarketData
from data_providers.data.schema.option_research import ensure
from data_providers.providers.theta_history import contract_id, quote, utc


def sample(**changes):
    return dict(timestamp='2024-04-12T12:00:00',bid='2.1',ask='2.2',bid_size='3',ask_size='4',**changes)


def test_nbbo_interval_is_not_a_fresh_quote():
    r=quote(sample(),cid='id',symbol='SPY',session='2024-04-12',receipt='hash')
    assert r[4].isoformat()=='2024-04-12T16:00:00+00:00'
    assert r[5] is None and r[6] is None and r[15] is None
    assert r[18]=='two_sided_age_unknown'
    assert utc('2024-01-12T12:00:00').hour==17


@pytest.mark.parametrize('bid,ask,quality',[(3,2,'crossed'),(2,2,'locked'),(0,2,'one_sided')])
def test_quality_is_retained(bid,ask,quality):
    row=sample();row.update(bid=bid,ask=ask)
    assert quote(row,cid='id',symbol='SPY',session='2024-04-12',receipt='hash')[18]==quality


@pytest.mark.parametrize('value',['-1','NaN','inf'])
def test_impossible_quotes_are_quarantined(value):
    row=sample();row['bid']=value
    with pytest.raises(ValueError):quote(row,cid='id',symbol='SPY',session='2024-04-12',receipt='hash')


def test_sdk_contract_filter_and_interval_end(tmp_path):
    with duckdb.connect(str(tmp_path/'market_history.duckdb')) as c:
        ensure(c)
        for cid in ['one','two']:
            row=quote(sample(),cid=cid,symbol='SPY',session='2024-04-12',receipt='hash')
            c.execute('insert into option_nbbo values ('+','.join('?' for _ in row)+')',row)
    client=MarketData(tmp_path)
    rows=client.read('us_option_quotes',contract_ids=['one'],end='2024-04-12')
    assert len(rows)==1 and rows[0]['contract_id']=='one' and rows[0]['quote_age_ms'] is None
    assert client.read('us_option_quotes',contract_ids=[])==[]
    assert client.read('us_option_quotes',contract_ids=["one' OR 1=1 --"])==[]
    assert client.read('us_option_quotes',contract_ids=['one'],end='2024-04-12T15:59:59Z')==[]
    with pytest.raises(ValueError):client.read('us_option_quotes',contract_ids='one')


def test_identity_strike_units():
    assert contract_id('SPY','2024-05-17','put','486.000')=='US:OPRA:SPY:2024-05-17:P:486'


def test_entitlement_refusal_is_retained_and_not_empty_success(tmp_path,monkeypatch):
    from data_providers.providers.theta_history import HistoryClient
    c=HistoryClient(tmp_path/'receipts','http://127.0.0.1:25503/v3')
    class Response:
        status_code=403
        content=b'Value subscription required'
        text=content.decode()
    monkeypatch.setattr(c.client.session,'get',lambda *a,**k:Response())
    monkeypatch.setattr(c.client,'_rate_limit',lambda:None)
    receipt,rows=c.request('option/history/quote',{'symbol':'SPY'})
    assert receipt['status']=='not_entitled' and rows==[]
    assert Path(receipt['raw_path']).read_bytes()==Response.content
    monkeypatch.setattr(c.client.session,'get',lambda *a,**k:pytest.fail('refusal must use checkpoint'))
    assert c.request('option/history/quote',{'symbol':'SPY'})[0]==receipt


def test_underlying_ohlc_and_volume_validation():
    from data_providers.providers.theta_history import validate_ohlcv
    row=dict(open='100',high='102',low='99',close='101',volume='500')
    assert validate_ohlcv(row)==[100,102,99,101,500]
    with pytest.raises(ValueError):validate_ohlcv(dict(row,high='99'))
    with pytest.raises(ValueError):validate_ohlcv(dict(row,volume='-1'))


@pytest.mark.parametrize('status', ['request_failed', 'invalid_response', 'in_flight', 'not_entitled', 'missing_upstream'])
@pytest.mark.parametrize('retry', [False, True])
def test_all_existing_receipts_block_replay(tmp_path, monkeypatch, status, retry):
    import hashlib, json
    from data_providers.providers.theta_history import HistoryClient
    c = HistoryClient(tmp_path/'receipts')
    params = {'symbol': 'SPXW', 'format': 'csv'}
    key = hashlib.sha256(json.dumps(['option/history/quote', params], sort_keys=True).encode()).hexdigest()
    p = c.folder/(key+'.json')
    original = json.dumps({'status': status, 'rows': 0}).encode()
    p.write_bytes(original)
    monkeypatch.setattr(c.client.session, 'get', lambda *a, **k: pytest.fail('existing key replayed'))
    assert c.request('option/history/quote', params, retry=retry)[1] == []
    assert p.read_bytes() == original


def test_interruption_leaves_durable_inflight_receipt(tmp_path, monkeypatch):
    import json
    from data_providers.providers.theta_history import HistoryClient
    c = HistoryClient(tmp_path/'receipts')
    monkeypatch.setattr(c.client, '_rate_limit', lambda: None)
    def interrupt(*a, **k):
        receipt = json.loads(next(c.folder.glob('*.json')).read_text())
        assert receipt['status'] == 'in_flight'
        raise KeyboardInterrupt()
    monkeypatch.setattr(c.client.session, 'get', interrupt)
    with pytest.raises(KeyboardInterrupt):
        c.request('option/history/quote', {'symbol': 'SPXW'})
    monkeypatch.setattr(c.client.session, 'get', lambda *a, **k: pytest.fail('uncertain request replayed'))
    assert c.request('option/history/quote', {'symbol': 'SPXW'})[0]['status'] == 'in_flight'


def test_request_timing_separates_queue_from_http_and_preserves_cache(tmp_path, monkeypatch):
    from datetime import timedelta
    from data_providers.providers import theta_history as mod
    client = mod.HistoryClient(tmp_path/'receipts')
    clock = iter([10.0, 13.0, 15.0, 16.0, 21.0])
    monkeypatch.setattr(mod.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(client.client, '_rate_limit', lambda: None)
    class Response:
        status_code = 200
        content = b'symbol,timestamp\nSPY,2024-04-12T12:00:00\n'
        text = content.decode()
        elapsed = timedelta(seconds=2)
    monkeypatch.setattr(client.client.session, 'get', lambda *a, **k: Response())
    receipt, rows = client.request('option/history/quote', {'symbol': 'SPY'})
    timing = receipt['timing']
    assert (timing['lock_wait_seconds'], timing['pacing_seconds'], timing['http_seconds']) == (3, 2, 5)
    assert timing['response_headers_seconds'] == 2
    assert timing['post_headers_seconds_estimate'] == 3
    assert timing['decoded_response_bytes'] == len(Response.content)
    monkeypatch.setattr(client.client.session, 'get', lambda *a, **k: pytest.fail('cache replay'))
    assert client.request('option/history/quote', {'symbol': 'SPY'}) == (receipt, rows)


def test_failed_request_retains_attempt_duration(tmp_path, monkeypatch):
    from data_providers.providers import theta_history as mod
    client = mod.HistoryClient(tmp_path/'receipts')
    clock = iter([1., 2., 3., 4., 11.])
    monkeypatch.setattr(mod.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(client.client, '_rate_limit', lambda: None)
    def fail(*args, **kwargs):
        raise TimeoutError('test')
    monkeypatch.setattr(client.client.session, 'get', fail)
    receipt, rows = client.request('option/history/quote', {'symbol': 'SPY'})
    assert receipt['status'] == 'request_failed' and not rows
    assert receipt['timing']['attempt_elapsed_seconds'] == 7
    assert 'http_completed_utc' not in receipt
    assert client.request('option/history/quote', {'symbol': 'SPY'}, retry=True)[0] == receipt


@pytest.mark.parametrize('value', [0, -1, 181, float('inf'), float('nan'), True, '180'])
def test_history_rejects_unbounded_timeout(tmp_path, value):
    from data_providers.providers.theta_history import HistoryClient
    with pytest.raises(ValueError):
        HistoryClient(tmp_path/'receipts', read_timeout=value)


def test_longer_timeout_does_not_change_key_or_replay_failure(tmp_path, monkeypatch):
    import json
    from data_providers.providers.theta_history import HistoryClient
    c = HistoryClient(tmp_path/'receipts', read_timeout=180)
    monkeypatch.setattr(c.client, '_rate_limit', lambda: None)
    def fail(*args, **kwargs):
        assert kwargs['timeout'] == (5, 180)
        saved = json.loads(next(c.folder.glob('*.json')).read_text())
        assert saved['status'] == 'in_flight'
        assert saved['timeout_policy']['read_idle_seconds'] == 180
        raise TimeoutError('synthetic idle timeout')
    monkeypatch.setattr(c.client.session, 'get', fail)
    receipt, rows = c.request('option/history/open_interest', {'symbol': 'SPXW'})
    assert receipt['status'] == 'request_failed' and rows == []
    default = HistoryClient(c.folder)
    monkeypatch.setattr(default.client.session, 'get', lambda *a, **k: pytest.fail('timeout change replayed key'))
    assert default.request('option/history/open_interest', {'symbol': 'SPXW'}, retry=True)[0] == receipt


def test_fresh_and_cached_theta_csv_preserve_unicode_and_embedded_newlines(tmp_path, monkeypatch):
    from data_providers.providers.theta_history import HistoryClient
    c = HistoryClient(tmp_path/'receipts')
    monkeypatch.setattr(c.client, '_rate_limit', lambda: None)
    class Response:
        status_code = 200
        content = 'symbol,timestamp,note\r\nSPY,2024-04-12T12:00:00,"café\r\nline two"\r\n'.encode()
        @property
        def text(self):
            raise AssertionError('Raw UTF-8 receipt is the decoding authority')
    monkeypatch.setattr(c.client.session, 'get', lambda *a, **k: Response())
    receipt, rows = c.request('option/history/quote', {'symbol': 'SPY'})
    assert receipt['status'] == 'downloaded'
    assert rows[0]['note'] == 'café\nline two'
    assert Path(receipt['raw_path']).read_bytes() == Response.content
    monkeypatch.setattr(c.client.session, 'get', lambda *a, **k: pytest.fail('cache replay'))
    assert c.request('option/history/quote', {'symbol': 'SPY'}) == (receipt, rows)
    Path(receipt['raw_path']).write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='checksum'):
        c.request('option/history/quote', {'symbol': 'SPY'})


def test_theta_does_not_accept_corrupt_preexisting_raw_file(tmp_path, monkeypatch):
    import hashlib, json
    from data_providers.providers.theta_history import HistoryClient
    c = HistoryClient(tmp_path/'receipts')
    monkeypatch.setattr(c.client, '_rate_limit', lambda: None)
    body = b'symbol,timestamp\nSPY,2024-04-12T12:00:00\n'
    params = {'symbol': 'SPY', 'format': 'csv'}
    key = hashlib.sha256(json.dumps(['option/history/quote', params], sort_keys=True).encode()).hexdigest()
    raw = c.folder/'raw'/(key+'-'+hashlib.sha256(body).hexdigest()+'.csv')
    raw.write_bytes(b'corrupted')
    class Response:
        status_code = 200
        content = body
    monkeypatch.setattr(c.client.session, 'get', lambda *a, **k: Response())
    receipt, rows = c.request('option/history/quote', params)
    assert receipt['status'] == 'request_failed' and rows == []
    assert raw.read_bytes() == b'corrupted'
    monkeypatch.setattr(c.client.session, 'get', lambda *a, **k: pytest.fail('failed key replay'))
    assert c.request('option/history/quote', params)[0] == receipt
