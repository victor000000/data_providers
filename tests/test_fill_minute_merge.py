import duckdb
import pytest
from data_providers.jobs.data_ops.merge_fill_minutes import merge


def setup(tmp_path):
    (tmp_path/'staging').mkdir()
    for path in [tmp_path/'market_history.duckdb',tmp_path/'staging/alpaca.duckdb']:
        with duckdb.connect(str(path)) as c:
            for table in ['option_minutes','underlying_minutes']:
                c.execute(f'CREATE TABLE {table}(source VARCHAR,symbol VARCHAR,ts_utc TIMESTAMP,close DOUBLE,PRIMARY KEY(source,symbol,ts_utc))')
    with duckdb.connect(str(tmp_path/'staging/alpaca.duckdb')) as c:
        c.execute("INSERT INTO option_minutes VALUES ('alpaca_1min','META','2026-10-23 19:46:00',36.35)")
    return tmp_path


def test_exact_merge_retains_stage_and_is_idempotent(tmp_path):
    home=setup(tmp_path)
    assert merge(home)['tables'][0]['new_rows']==1
    assert merge(home)['tables'][0]['new_rows']==0
    assert (home/'staging/alpaca.duckdb').exists()
    with duckdb.connect(str(home/'market_history.duckdb')) as c:
        assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='incoming'").fetchone()[0]==0


def test_conflict_preserves_both_versions(tmp_path):
    home=setup(tmp_path)
    with duckdb.connect(str(home/'market_history.duckdb')) as c:
        c.execute("INSERT INTO option_minutes VALUES ('alpaca_1min','META','2026-10-23 19:46:00',99)")
    with pytest.raises(ValueError):merge(home)
    with duckdb.connect(str(home/'market_history.duckdb')) as c:
        assert c.execute('SELECT close FROM option_minutes').fetchone()[0]==99
    assert (home/'staging/alpaca.duckdb').exists()
