from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from data_providers.jobs.history.calculate_daily_pv import build, calculate, merge_stage


def inputs(con):
    con.execute('CREATE TABLE inputs(market VARCHAR,venue VARCHAR,instrument_id VARCHAR,symbol VARCHAR,'
                'contract_id VARCHAR,day DATE,source VARCHAR,close DOUBLE,volume DOUBLE,'
                'volume_unit VARCHAR,price_convention VARCHAR)')


def test_pv_hand_values_previous_volume_window_and_contract_partitioning():
    with duckdb.connect() as con:
        inputs(con)
        start = date(2026, 1, 1)
        for i in range(22):
            con.execute('INSERT INTO inputs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        ['US', 'UNKNOWN', 'US:SPY', 'SPY', None, start + timedelta(days=i),
                         'yahoo', 100 + i, i + 1 if i < 20 else 999, 'shares', 'adjusted'])
        for contract in ['M2601', 'M2605']:
            con.execute('INSERT INTO inputs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        ['CN', 'DCE', 'CN:DCE:' + contract, 'M', contract, start,
                         'sina_contract', 3000, 10, 'contracts', 'raw'])
        calculate(con, 'pinned', datetime(2026, 1, 25, tzinfo=UTC))
        get = lambda field: con.execute('SELECT value FROM daily_indicators WHERE instrument_id=? AND day=? AND field_id=?',
                                       ['US:SPY', start + timedelta(days=20), field]).fetchone()[0]
        assert get('prior_20_observation_mean_volume') == 10.5
        assert get('close_return_since_previous_observation') == pytest.approx(120 / 119 - 1)
        assert get('realized_volatility_20_observation') > 0
        assert con.execute("SELECT count(value) FROM daily_indicators WHERE market='CN'").fetchone() == (0,)
        assert con.execute('SELECT count(*) FROM daily_indicators WHERE available_at IS NOT NULL').fetchone() == (0,)


def test_duplicate_source_dates_rejected():
    with duckdb.connect() as con:
        inputs(con)
        con.execute("INSERT INTO inputs VALUES ('US','X','SPY','SPY',NULL,'2026-01-02','y',100,1,'shares','raw')")
        con.execute('INSERT INTO inputs SELECT * FROM inputs')
        with pytest.raises(ValueError, match='Duplicate'):
            calculate(con, 'pinned', datetime.now(UTC))


def test_indicator_stage_merge_retains_rows_and_replays_without_duplicates(tmp_path):
    stage = tmp_path / 'stage.duckdb'
    with duckdb.connect(str(stage)) as con:
        inputs(con)
        con.execute("INSERT INTO inputs VALUES ('US','X','SPY','SPY',NULL,'2026-01-02','y',100,1,'shares','raw')")
        calculate(con, 'pinned', datetime.now(UTC))
    merge_stage(tmp_path, stage)
    merge_stage(tmp_path, stage)
    with duckdb.connect(str(tmp_path / 'market_history.duckdb'), read_only=True) as c:
        assert c.execute('SELECT count(*) FROM daily_indicators').fetchone() == (3,)
    assert stage.exists()


def test_both_market_input_queries_merge_and_publish_through_real_sdk(tmp_path):
    from data_providers import MarketData
    from data_providers.lake import STORES, publish
    for store, filename in STORES.items():
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(path)) as con:
            if store == 'universe':
                con.execute("CREATE TABLE prices(symbol VARCHAR,date DATE,adj_close DOUBLE,volume BIGINT)")
                con.execute("INSERT INTO prices VALUES ('SPY','2026-01-02',100,10),('QQQ','2026-01-02',200,20)")
            elif store == 'cn':
                con.execute('CREATE TABLE futures_daily(venue VARCHAR,product VARCHAR,contract VARCHAR,'
                            'day DATE,source VARCHAR,close DOUBLE,volume DOUBLE)')
                con.execute("INSERT INTO futures_daily VALUES ('DCE','M','m2605','2026-01-02','sina_contract',3000,100),"
                            "('DCE','M','M0','2026-01-02','sina_main',2900,500)")
            else:
                con.execute('CREATE TABLE empty(value DOUBLE)')
    publish(tmp_path)
    folder, report = build(tmp_path)
    assert {r[0] for r in report['market_field_counts_rows_valid_first_last_instruments']} == {'US', 'CN'}
    assert (folder / 'report.json').is_file()
    publish(tmp_path)
    records = MarketData(tmp_path, backend='parquet').read('daily_indicators')
    assert len(records) == 9
    assert {r['instrument_id'] for r in records} == {'US:EQUITY:SPY', 'US:EQUITY:QQQ', 'CN:DCE:FUT:M2605'}
    assert all(r['available_at'] is None for r in records)
