import json

import duckdb
import pytest

from data_providers import MarketData
from data_providers.cli import main
from data_providers.lake import STORES, publish


@pytest.fixture
def sample(tmp_path):
    con = duckdb.connect(str(tmp_path / 'market_history.duckdb'))
    con.execute('CREATE TABLE underlying_daily(day DATE, symbol TEXT, source TEXT, close DOUBLE)')
    con.execute("INSERT INTO underlying_daily VALUES ('2026-11-02','SPY','a',10),"
                "('2026-11-02','SPY','a',10), ('2026-11-03','SPY','b',11),"
                "('2026-11-04','SPY','a',12), ('2026-11-03','QQQ','a',NULL),"
                "('2026-11-04','QQQ','a','NaN'::DOUBLE)")
    con.close()
    return MarketData(tmp_path)


def contract(**changes):
    return dict(version=1, dataset='us_daily', symbols=['SPY', 'QQQ'],
                sessions=['2026-11-02', '2026-11-03', '2026-11-04'],
                filters={'source': 'a'}, required_columns=['close'], **changes)


def test_holes_source_isolation_quality_and_duplicates(sample):
    result = sample.readiness(contract())
    qqq, spy = result['symbols']
    assert spy['missing_sessions'] == ['2026-11-03']
    assert spy['qualifying_rows'] == 3
    assert len(qqq['missing_sessions']) == 3
    assert result['status'] == 'provisional' and not result['ready']
    c = contract()
    c['sessions'] = ['2026-11-02', '2026-11-04']
    c['symbols'] = ['SPY']
    result = sample.readiness(c)
    assert result['coverage_complete'] and not result['ready']
    c['sessions'].reverse()
    assert sample.readiness(c)['contract_sha256'] == result['contract_sha256']


@pytest.mark.parametrize('change', [
    {'version': True}, {'version': 2}, {'filters': {}}, {'filters': {'source': None}},
    {'symbols': []}, {'sessions': ['20261102']}, {'sessions': ['2026-11-02'] * 2},
    {'required_columns': ['bad']}, {'filters': {'source': 'a', 'bad': 1}}, {'unknown': 1},
])
def test_invalid_contracts_fail(sample, change):
    c = contract()
    c.update(change)
    with pytest.raises(ValueError):
        sample.readiness(c)


def test_no_creation_and_private_access(tmp_path):
    client = MarketData(tmp_path)
    with pytest.raises(FileNotFoundError):
        client.readiness(contract())
    c = contract()
    c['dataset'] = 'fills'
    with pytest.raises(PermissionError):
        client.readiness(c)
    assert list(tmp_path.iterdir()) == []


def test_pinned_release_and_cli(sample, tmp_path, capsys):
    for store, filename in STORES.items():
        path = tmp_path / filename
        if store != 'trades' and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            con = duckdb.connect(str(path))
            con.execute('CREATE TABLE placeholder(id INTEGER)')
            con.close()
    publish(tmp_path)
    pinned = MarketData(tmp_path)
    c = contract()
    c.update(symbols=['SPY'], sessions=['2026-11-02'])
    result = pinned.readiness(c)
    assert result['ready'] and result['release'] == pinned.release.id
    assert result['release_integrity'] == 'not_assessed'
    con = duckdb.connect(str(tmp_path / 'market_history.duckdb'))
    con.execute('DELETE FROM underlying_daily')
    con.close()
    publish(tmp_path)
    assert pinned.readiness(c)['ready']
    assert not MarketData(tmp_path).readiness(c)['ready']
    path = tmp_path / 'contract.json'
    path.write_text(json.dumps(c))
    capsys.readouterr()
    assert main(['--home', str(tmp_path), '--release', pinned.release.id, 'readiness', str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['ready']


def test_timestamp_sessions_use_utc_and_filters_are_parameters(tmp_path):
    con = duckdb.connect(str(tmp_path / 'market_history.duckdb'))
    con.execute('CREATE TABLE underlying_minutes(ts_utc TIMESTAMPTZ, symbol TEXT, source TEXT, close DOUBLE)')
    con.execute("INSERT INTO underlying_minutes VALUES ('2026-11-03 01:00:00+08','SPY','a',10)")
    con.close()
    client = MarketData(tmp_path)
    c = contract()
    c.update(dataset='us_minutes', symbols=['SPY'], sessions=['2026-11-02'])
    assert client.readiness(c)['coverage_complete']
    c['filters'] = {'source': "a' OR true --"}
    assert not client.readiness(c)['coverage_complete']
    c['filters'] = {'source': 'a'}
    assert client.readiness(c)['coverage_complete']


def test_method_filters_do_not_pool_partial_histories(tmp_path):
    con = duckdb.connect(str(tmp_path / 'market_history.duckdb'))
    con.execute('CREATE TABLE underlying_daily(day DATE, symbol TEXT, source TEXT, method TEXT, close DOUBLE)')
    con.execute("INSERT INTO underlying_daily VALUES ('2026-11-02','SPY','a','native',10),"
                "('2026-11-03','SPY','a','derived',11)")
    con.close()
    c = contract()
    c.update(symbols=['SPY'], sessions=['2026-11-02', '2026-11-03'],
             filters={'source': 'a', 'method': 'native'})
    report = MarketData(tmp_path).readiness(c)
    assert report['symbols'][0]['missing_sessions'] == ['2026-11-03']
    assert not report['coverage_complete']


def test_contract_limits_fail_before_opening_stores(tmp_path):
    c = contract()
    c.update(symbols=[f'S{i}' for i in range(101)],
             sessions=[f'{year}-01-01' for year in range(1, 10001)])
    with pytest.raises(ValueError, match='one million'):
        MarketData(tmp_path).readiness(c)
    assert list(tmp_path.iterdir()) == []


def test_optional_quality_columns_and_empty_dataset(sample):
    c = contract()
    c.update(symbols=['QQQ'], sessions=['2026-11-03'])
    del c['required_columns']
    assert sample.readiness(c)['coverage_complete']
    c['symbols'] = ['ABSENT']
    report = sample.readiness(c)
    assert report['symbols'][0]['qualifying_rows'] == 0
    assert report['symbols'][0]['missing_sessions'] == ['2026-11-03']
