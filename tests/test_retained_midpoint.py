import json
from datetime import timedelta
import duckdb
import pytest
from data_providers import MarketData
from data_providers.data.schema.option_research import ensure
from data_providers.data.schema.history import ensure as ensure_history
from data_providers.providers.retained_midpoint import normalize


def receipt():
    bar = dict(open=2, high=3, low=1, close=2.5, volume=-1, average=-1)
    return dict(kind='MIDPOINT', bar_size='1 hour', received_utc='2026-10-24T02:00:00Z',
                contract=dict(secType='OPT', currency='USD', conId=123, multiplier='100',
                              strike=110, right='C', symbol='SWKS', lastTradeDateOrContractMonth='20261120'),
                bars=[dict(bar, date='2026-09-29T13:30:00Z'), dict(bar, date='2026-09-29T14:00:00Z')])


def test_partial_opening_bar_and_unknown_last_end(tmp_path):
    rows = normalize(json.dumps(receipt()).encode())
    assert rows[0][10] - rows[0][9] == timedelta(minutes=30)
    assert rows[0][11] == 3600 and rows[1][10] is None
    assert all(r[18] is None and r[19] is None and r[20] is None for r in rows)
    with duckdb.connect(str(tmp_path / 'market_history.duckdb')) as c:
        ensure_history(c, prefix='')
        ensure(c)
        for _ in range(2):
            c.executemany('INSERT INTO option_reference_bars VALUES (' + ','.join('?' for _ in rows[0]) + ') ON CONFLICT DO NOTHING', rows)
    client = MarketData(home=tmp_path, backend='duckdb')
    saved = client.read('us_option_reference_bars', contract_ids=['IBKR:123'])
    assert len(saved) == 2 and saved[0]['price_basis'] == 'MIDPOINT'
    assert client.read('us_option_minutes') == []


@pytest.mark.parametrize('problem', ['naive', 'duplicate', 'wrong_kind', 'missing_identity', 'crossed_ohlc'])
def test_reject_ambiguous_or_invalid_receipts(problem):
    d = receipt()
    if problem == 'naive': d['bars'][0]['date'] = '2026-09-29T13:30:00'
    if problem == 'duplicate': d['bars'][1]['date'] = d['bars'][0]['date']
    if problem == 'wrong_kind': d['kind'] = 'TRADES'
    if problem == 'missing_identity': d['contract']['conId'] = 0
    if problem == 'crossed_ohlc': d['bars'][0]['low'] = 4
    with pytest.raises(ValueError): normalize(json.dumps(d).encode())


def test_metadata_reuse_requires_same_contract():
    d = receipt()
    metadata = json.dumps(d).encode()
    d['contract']['currency'] = ''
    d['contract']['multiplier'] = ''
    rows = normalize(json.dumps(d).encode(), metadata_payload=metadata)
    assert rows[0][6:8] == (100, 'USD')
    assert json.loads(rows[0][-1])['metadata_receipt_sha256']
    d['contract']['conId'] = 456
    with pytest.raises(ValueError, match='identity'):
        normalize(json.dumps(d).encode(), metadata_payload=metadata)
