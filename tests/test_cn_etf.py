import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from data_providers.providers.cn_etf import fetch, parse


def body(**overrides):
    data = {'code':'510050','market':1,'klines':['2026-10-16,2.878,2.866,2.898,2.844,18804281,5401255542,1.88,-0.31,-0.009,24.73']}
    data.update(overrides)
    return json.dumps({'data':data}).encode()


def test_cash_etf_identity_adjustment_units_and_unknown_availability():
    receipt = {'parameters':{'fqt':'0'},'retrieved_at':datetime.now(UTC).isoformat()}
    rows = parse(body(),receipt,'receipt.json',start=date(2026,10,1),end=date(2026,10,16),adjustment='raw')
    assert rows[0][1:4] == ('SSE','CN:SSE:ETF:510050','510050')
    assert rows[0][7:11] == (2.878,2.898,2.844,2.866)
    assert rows[0][11:16] == (18804281,'vendor_native_unverified',5401255542,None,None)
    with pytest.raises(ValueError,match='Adjustment'):
        parse(body(),receipt,'r',start=date(2026,10,1),end=date(2026,10,16),adjustment='qfq')
    with pytest.raises(ValueError,match='identity'):
        parse(body(code='510300'),receipt,'r',start=date(2026,10,1),end=date(2026,10,16),adjustment='raw')


def test_bad_geometry_duplicate_dates_and_empty_payload_fail_closed():
    receipt = {'parameters':{'fqt':'0'},'retrieved_at':datetime.now(UTC).isoformat()}
    kwargs = {'start': date(2026,10,1),'end': date(2026,10,16),'adjustment': 'raw'}
    with pytest.raises(ValueError,match='OHLC'):
        parse(body(klines=['2026-10-16,3,3,2,1,100,300,0,0,0,0']),receipt,'r',**kwargs)
    data = json.loads(body())['data']
    with pytest.raises(ValueError,match='Duplicate'):
        parse(body(klines=data['klines']*2),receipt,'r',**kwargs)
    with pytest.raises(ValueError,match='No ETF'):
        parse(body(klines=[]),receipt,'r',**kwargs)


def test_successful_body_resume_and_corruption_detection(tmp_path):
    class Session:
        calls = 0
        def get(self,*args,**kwargs):
            self.calls += 1
            return SimpleNamespace(content=body(),url='https://source.example',status_code=200,
                                   raise_for_status=lambda:None)
    s = Session()
    kwargs = {'start': date(2026,10,1),'end': date(2026,10,16),'adjustment': 'raw','session': s}
    _,receipt,_ = fetch(tmp_path,**kwargs)
    fetch(tmp_path,**kwargs)
    assert s.calls == 1
    (tmp_path/receipt['body_file']).write_bytes(b'broken')
    with pytest.raises(ValueError,match='checksum'):
        fetch(tmp_path,**kwargs)


def test_raw_equity_merge_is_exact_and_idempotent(tmp_path):
    import duckdb

    from data_providers.data.schema.cn_equities import ensure
    from data_providers.jobs.history.maintain_cn_etf import merge_raw
    receipt = {'parameters':{'fqt':'0'},'retrieved_at':datetime.now(UTC).isoformat()}
    rows = parse(body(),receipt,'receipt.json',start=date(2026,10,1),end=date(2026,10,16),adjustment='raw')
    path = tmp_path/'stage.duckdb'
    with duckdb.connect(str(path)) as con:
        ensure(con)
        con.executemany('INSERT INTO equity_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
    merge_raw(tmp_path,path)
    merge_raw(tmp_path,path)
    with duckdb.connect(str(tmp_path/'cn_market.duckdb'),read_only=True) as con:
        assert con.execute('SELECT count(*),max(close),max(volume_unit) FROM equity_daily').fetchone() == (1,2.866,'vendor_native_unverified')


@pytest.mark.parametrize('outcome', ['denied', 'empty', 'invalid', 'timeout', 'interrupted'])
def test_every_attempt_is_protected_from_replay(tmp_path, outcome):
    import requests
    from data_providers.providers.cn_etf import ProtectedETFRequest

    class Session:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            # The durable intent must exist before any network dispatch.
            receipts = list(tmp_path.glob('*.json'))
            assert len(receipts) == 1
            assert json.loads(receipts[0].read_text())['status'] == 'in_flight'
            if outcome == 'timeout':
                raise requests.Timeout()
            if outcome == 'interrupted':
                raise KeyboardInterrupt()
            content = body(klines=[]) if outcome == 'empty' else b'not json' if outcome == 'invalid' else b'denied'

            def raise_status():
                if outcome == 'denied':
                    raise requests.HTTPError('403')

            return SimpleNamespace(content=content, url='https://source.example',
                                   status_code=403 if outcome == 'denied' else 200,
                                   raise_for_status=raise_status)

    session = Session()
    kwargs = dict(start=date(2026, 10, 1), end=date(2026, 10, 16),
                  adjustment='raw', session=session)
    try:
        fetch(tmp_path, **kwargs)
    except (requests.RequestException, KeyboardInterrupt):
        pass
    before = {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
    with pytest.raises(ProtectedETFRequest):
        fetch(tmp_path, **kwargs)
    assert session.calls == 1
    assert before == {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}


def test_legacy_error_without_current_receipt_is_not_retried(tmp_path):
    import requests
    from data_providers.providers.cn_etf import ProtectedETFRequest

    class Session:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            raise requests.Timeout()

    session = Session()
    kwargs = dict(start=date(2026, 10, 1), end=date(2026, 10, 16),
                  adjustment='raw', session=session)
    with pytest.raises(requests.Timeout):
        fetch(tmp_path, **kwargs)
    # Reproduce the legacy format: only a timestamped exception receipt exists.
    next(p for p in tmp_path.glob('*.json') if len(p.stem) == 64).unlink()
    with pytest.raises(ProtectedETFRequest, match='Legacy'):
        fetch(tmp_path, **kwargs)
    assert session.calls == 1


def test_concurrent_same_key_dispatches_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time

    start = threading.Barrier(2)

    class Session:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            time.sleep(.05)
            return SimpleNamespace(content=body(), url='https://source.example',
                                   status_code=200, raise_for_status=lambda: None)

    session = Session()

    def run():
        start.wait(timeout=5)
        return fetch(tmp_path, start=date(2026, 10, 1), end=date(2026, 10, 16),
                     adjustment='raw', session=session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert session.calls == 1
    assert results[0] == results[1]


def test_retrying_transport_rejected_before_intent_or_http(tmp_path):
    import requests

    with requests.Session() as session:
        session.mount('https://', requests.adapters.HTTPAdapter(max_retries=3))
        with pytest.raises(ValueError, match='retries disabled'):
            fetch(tmp_path, start=date(2026, 10, 1), end=date(2026, 10, 16),
                  adjustment='raw', session=session)
    assert not list(tmp_path.glob('*.json'))
