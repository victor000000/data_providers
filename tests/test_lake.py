from datetime import date

import duckdb
import pytest

from data_providers.client import MarketData
from data_providers.lake import STORES, Release, publish, verify


@pytest.fixture
def warehouse(tmp_path):
    for name, filename in STORES.items():
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(path)) as con:
            if name == 'universe':
                con.execute('CREATE TABLE prices(symbol VARCHAR, date DATE, close DOUBLE, adj_close DOUBLE, source VARCHAR)')
                con.execute("INSERT INTO prices VALUES ('SPY','2025-01-02',100,99,'yahoo'), ('SPY','2026-01-02',105,104,'yahoo')")
            else:
                con.execute('CREATE TABLE sample(day DATE, value DOUBLE)')
                con.execute("INSERT INTO sample VALUES (NULL,NULL), ('2026-01-02',42)")
    return tmp_path


def test_release_pinning_incremental_and_private(warehouse):
    first = publish(warehouse)
    client = MarketData(warehouse)
    assert client.backend == 'parquet'
    status = client.status('us_prices')
    assert status['rows'] == 2 and status['time_max'] == '2026-01-02'
    assert status['release'] == first['release']
    assert client.read('us_prices')[0]['date'] == date(2025, 1, 2)
    with pytest.raises(PermissionError):
        client.read('fills')
    with duckdb.connect(str(warehouse / STORES['universe'])) as con:
        con.execute("UPDATE prices SET close=110 WHERE year(date)=2026")
    second = publish(warehouse, private=True)
    assert first['release'] != second['release']
    assert client.read('us_prices')[-1]['close'] == 105
    assert MarketData(warehouse).read('us_prices')[-1]['close'] == 110
    a = first['stores']['universe']['tables']['prices']['partitions']
    b = second['stores']['universe']['tables']['prices']['partitions']
    assert a[0]['path'] == b[0]['path']  # 2025 partition reused, 2026 replaced
    assert a[1]['path'] != b[1]['path']
    assert verify(warehouse)['ok']
    assert 'trades' not in first['stores']
    assert 'trades' in second['stores']
    out = warehouse / 'export'
    exported = MarketData(warehouse).export('us_prices', out)
    assert exported['release'] == second['release']
    assert exported['backend'] == 'parquet'


def test_failed_publication_keeps_current_and_failed_artifacts(warehouse):
    first = publish(warehouse)
    (warehouse / STORES['cn']).unlink()
    with pytest.raises(FileNotFoundError):
        publish(warehouse)
    assert Release(warehouse).id == first['release']
    assert list((warehouse / 'lake/snapshots').glob('.incomplete-*/failure.json'))
    assert MarketData(warehouse).read('us_prices')


def test_tampering_and_missing_never_fall_back(warehouse):
    manifest = publish(warehouse)
    partition = manifest['stores']['universe']['tables']['prices']['partitions'][0]
    path = warehouse / 'lake' / partition['path']
    path.write_bytes(b'broken')
    with pytest.raises(ValueError, match='Checksum'):
        verify(warehouse)
    (warehouse / 'lake/CURRENT').write_text('../escape')
    with pytest.raises(ValueError):
        MarketData(warehouse)


def test_missing_reads_do_not_create_runtime(tmp_path):
    home = tmp_path / 'missing'
    with pytest.raises(FileNotFoundError):
        MarketData(home, backend='parquet')
    with pytest.raises(FileNotFoundError):
        MarketData(home).read('us_prices')
    assert not home.exists()


def test_source_month_iv_storage_and_tagged_projection(warehouse):
    from data_providers.provenance import ensure
    with duckdb.connect(str(warehouse / STORES['history'])) as con:
        ensure(con)
        for source, day, value in [('broker', '2026-09-30', .2),
                                   ('broker', '2026-10-01', .3),
                                   ('vendor', '2026-10-01', .4)]:
            con.execute('''INSERT INTO iv_observations_v2
                (instrument_id,stored_underlying,market,day,source,datasource_tag,
                 method_id,iv_value,provenance_status)
                 VALUES ('SPY','SPY','US',?,?,?,?,?,'legacy_recorded_unlinked')''',
                        [day, source, source, 'native_v1', value])
    first = publish(warehouse)
    table = first['stores']['history']['tables']['iv_observations_v2']
    assert table['partition_granularity'] == 'datasource_month'
    assert len(table['partitions']) == 3
    assert all('datasource=' in part['path'] for part in table['partitions'])
    for backend in ['duckdb', 'parquet']:
        rows = MarketData(warehouse, backend=backend).read_tagged(
            'iv_observations_v2', source='vendor', columns=['iv_value'])
        assert rows[0]['iv_value'] == .4
        assert rows[0]['datasource_tag'] == 'vendor'
        assert rows[0]['consistency_tag'] == 'cross_source_inconsistent'
        assert rows[0]['comparison_status'] == 'not_compared'
    with duckdb.connect(str(warehouse / STORES['history'])) as con:
        con.execute("UPDATE iv_observations_v2 SET iv_value=.5 WHERE source='broker' AND day='2026-10-01'")
    second = publish(warehouse)
    after = second['stores']['history']['tables']['iv_observations_v2']['partitions']
    paths = {(p['datasource_tag'], p['month']): p['path'] for p in table['partitions']}
    reused = sum(p['path'] == paths[p['datasource_tag'], p['month']] for p in after)
    assert reused == 2
    assert verify(warehouse)['ok']


@pytest.mark.parametrize('table', ['us_futures_daily', 'us_futures_option_settlements'])
def test_explicit_settlement_receipt_membership_is_pinned_and_unambiguous(warehouse, table):
    import json
    from data_providers.provenance import ensure
    with duckdb.connect(str(warehouse / STORES['history'])) as con:
        ensure(con)
        con.execute(f"CREATE TABLE {table}(source VARCHAR, contract_id VARCHAR, day DATE, settle DOUBLE, root VARCHAR DEFAULT 'VIX')")
        con.executemany(f'INSERT INTO {table}(source,contract_id,day,settle) VALUES (?,?,?,?)',
                        [('cboe', name, '2026-10-23', 10.0) for name in 'ABCDEFGH'])

        def receipt(rid, contracts, *, source='cboe', day='2026-10-23', dataset=table, raw='a'*64):
            con.execute('''INSERT INTO source_receipts VALUES
                (?,?, 'cboe','endpoint',?, 'raw',?, 'receipt',?,NULL,'raw_checksum_verified')''',
                        [rid, source, json.dumps({'dataset': dataset, 'day': day,
                                                  'contract_ids': contracts}), raw, 'b'*64])
        receipt('unique', ['A'])
        receipt('wrong_source', ['C'], source='other')
        receipt('wrong_day', ['D'], day='2026-10-22')
        receipt('ambiguous1', ['E'])
        receipt('ambiguous2', ['E'])
        receipt('wrong_dataset', ['F'], dataset='unrelated')
        receipt('unverified_raw', ['G'], raw=None)
        receipt('malformed_membership', 'H')
    release = publish(warehouse)['release']
    for backend in ['duckdb', 'parquet']:
        client = MarketData(warehouse, backend=backend)
        original = client.read(table)
        assert all('source_receipt_id' not in row for row in original)
        rows = client.read_tagged(table, columns=['contract_id', 'settle'])
        linked = {row['contract_id']: row for row in rows}
        assert linked['A']['source_receipt_id'] == 'unique'
        assert linked['A']['provenance_status'] == 'explicit_receipt_membership_linked'
        assert all(linked[name]['source_receipt_id'] is None for name in 'BCDEFGH')
        assert all(row['datasource_tag'] == 'cboe' for row in rows)
    with duckdb.connect(str(warehouse / STORES['history'])) as con:
        con.execute("INSERT INTO source_receipts SELECT 'later',datasource_tag,provider,endpoint,"
                    "native_parameters,raw_path,raw_sha256,original_receipt_path,"
                    "original_receipt_sha256,received_at,provenance_status FROM source_receipts "
                    "WHERE receipt_id='unique'")
    assert MarketData(warehouse, backend='duckdb').read_tagged(table)[0]['source_receipt_id'] is None
    assert MarketData(warehouse, backend='parquet', release=release).read_tagged(table)[0]['source_receipt_id'] == 'unique'


def test_parquet_readers_do_not_lock_ingestion(warehouse):
    from data_providers.lake import connect_store
    publish(warehouse)
    reader = connect_store(warehouse, "universe", backend="parquet")
    try:
        with duckdb.connect(str(warehouse / STORES['universe'])) as writer:
            writer.execute("UPDATE prices SET close=999")
        assert reader.execute('SELECT max(close) FROM prices').fetchone()[0] == 105
    finally:
        reader.close()


def test_empty_tables_types_and_sql_aliases(warehouse, monkeypatch):
    from data_providers.data import db
    with duckdb.connect(str(warehouse / STORES['reference'])) as con:
        con.execute('CREATE TABLE empty(ts TIMESTAMPTZ, payload BLOB, amount DECIMAL(18,6))')
        con.execute('CREATE TABLE special(payload JSON, n HUGEINT, raw BLOB, ts TIMESTAMPTZ, amount DECIMAL(18,6))')
        con.execute("INSERT INTO special VALUES ('{\"a\": 1}', 12345678901234567890123456789, '\\x00\\xff', '2026-01-02 09:00:00.123456+08', 42.123456)")
        con.execute('CREATE VIEW projected AS SELECT * FROM special')
    publish(warehouse, private=True)
    monkeypatch.setattr(db, 'ROOT', warehouse)
    with db.connect(log='ro', history='ro', reference='ro', universe=True) as con:
        assert con.execute('SELECT count(*) FROM yh.prices').fetchone()[0] == 2
        assert con.execute('SELECT count(*) FROM mh.sample').fetchone()[0] == 2
        assert con.execute('SELECT count(*) FROM ref.empty').fetchone()[0] == 0
        assert con.execute('SELECT typeof(payload),n FROM ref.projected').fetchone() == ('JSON',12345678901234567890123456789)
        assert con.execute("SELECT raw, strftime(ts, '%H:%M:%S.%f'), amount::VARCHAR FROM ref.projected").fetchone() == (b'\x00\xff', '01:00:00.123456', '42.123456')
        assert con.execute('SELECT count(*) FROM sample').fetchone()[0] == 2


def test_publish_lock_and_atomic_failure(warehouse, monkeypatch):
    import fcntl

    from data_providers import lake
    first = publish(warehouse)
    with (warehouse / 'var/maintenance/writer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='active'):
            publish(warehouse)
    original = lake.os.replace
    def fail_pointer(src, dst):
        if str(dst).endswith('/CURRENT'):
            raise OSError('injected pointer failure')
        return original(src, dst)
    monkeypatch.setattr(lake.os, 'replace', fail_pointer)
    with pytest.raises(OSError, match='injected'):
        publish(warehouse)
    assert Release(warehouse).id == first['release']
    assert verify(warehouse)['ok']


def test_release_manifest_paths_cannot_escape(warehouse):
    import json
    manifest = publish(warehouse)
    path = warehouse / 'lake/snapshots' / manifest['release'] / 'manifest.json'
    manifest['stores']['universe']['tables']['prices']['partitions'][0]['path'] = '../../outside.parquet'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='escapes'):
        MarketData(warehouse).read('us_prices')


def test_breaking_schema_requires_explicit_migration(warehouse):
    first = publish(warehouse)
    with duckdb.connect(str(warehouse / STORES['universe'])) as con:
        con.execute('ALTER TABLE prices DROP COLUMN adj_close')
    with pytest.raises(ValueError, match='Breaking schema'):
        publish(warehouse)
    assert Release(warehouse).id == first['release']
    migrated = publish(warehouse, allow_schema_change=True)
    assert migrated['release'] != first['release']


def test_daily_exact_comparison_preserves_nulls_extras_and_multiplicity():
    from data_providers.lake import exact_mismatch
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        con.execute('CREATE TABLE a(ts TIMESTAMPTZ, value INTEGER)')
        con.execute("INSERT INTO a VALUES ('2026-01-01 23:59:59+00',1), ('2026-01-02 00:00:00+00',2), (NULL,3), (NULL,3)")
        con.execute('CREATE TABLE b AS SELECT * FROM a')
        check = lambda: exact_mismatch(con, 'SELECT * FROM a', 'SELECT * FROM b', time_col='ts', rows=250001)
        assert not check()
        con.execute('INSERT INTO b VALUES (NULL,3)')
        assert check()
        con.execute('DELETE FROM b'); con.execute('INSERT INTO b SELECT * FROM a')
        con.execute("INSERT INTO b VALUES ('2026-02-01 00:00:00+00',4)")
        assert check()
        con.execute('DELETE FROM b'); con.execute('INSERT INTO b SELECT * FROM a')
        con.execute('UPDATE b SET value=9 WHERE value=2')
        assert check()
        assert exact_mismatch(con, 'SELECT * FROM a', 'SELECT * FROM b')


@pytest.mark.parametrize('timezone', ['UTC', 'America/New_York'])
def test_daily_comparison_range_preserves_timezone_boundaries(timezone):
    from data_providers.lake import exact_mismatch
    with duckdb.connect() as con:
        con.execute(f"SET TimeZone='{timezone}'")
        con.execute('CREATE TABLE a(ts TIMESTAMPTZ, value INTEGER)')
        con.execute("""INSERT INTO a VALUES
            ('2026-03-08 04:59:59+00', 1),
            ('2026-03-08 05:00:00+00', 2),
            ('2026-03-09 03:59:59+00', 3),
            ('2026-03-09 04:00:00+00', 4)""")
        con.execute('CREATE TABLE b AS SELECT * FROM a')
        assert not exact_mismatch(con, 'SELECT * FROM a', 'SELECT * FROM b', time_col='ts', rows=250001)
        for value in range(1, 5):
            con.execute('DELETE FROM b')
            con.execute('INSERT INTO b SELECT * FROM a WHERE value != ?', [value])
            assert exact_mismatch(con, 'SELECT * FROM a', 'SELECT * FROM b', time_col='ts', rows=250001)


def test_unchanged_partitions_skip_row_level_rescan(warehouse, monkeypatch):
    from data_providers import lake
    publish(warehouse)
    calls = []
    real = lake.exact_mismatch

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(lake, "exact_mismatch", spy)
    publish(warehouse)
    assert calls == []


def test_option_daily_publication_reuses_days_and_preserves_null_duplicates(warehouse):
    from data_providers.lake import connect_store
    with duckdb.connect(str(warehouse / STORES['history'])) as con:
        con.execute('CREATE TABLE option_nbbo(source VARCHAR, underlying VARCHAR, interval_ts TIMESTAMPTZ, bid DOUBLE)')
        con.execute("""INSERT INTO option_nbbo VALUES
            ('theta','SPX','2026-03-08 23:59:59+00',1),
            ('theta','SPX','2026-03-09 00:00:00+00',2),
            ('theta','SPX',NULL,3), ('theta','SPX',NULL,3)""")
    first = publish(warehouse)
    a = first['stores']['history']['tables']['option_nbbo']
    assert a['partition_granularity'] == 'utc_day'
    assert {p['day'] for p in a['partitions']} == {'2026-03-08', '2026-03-09', None}
    with duckdb.connect(str(warehouse / STORES['history'])) as con:
        con.execute("UPDATE option_nbbo SET bid=4 WHERE bid=2")
        con.execute("INSERT INTO option_nbbo VALUES ('theta','SPX','2026-03-10 00:00:00+00',5)")
    events = []
    second = publish(warehouse, progress=events.append)
    old = {p['day']: p['path'] for p in a['partitions']}
    new = {p['day']: p['path'] for p in second['stores']['history']['tables']['option_nbbo']['partitions']}
    assert new['2026-03-08'] == old['2026-03-08'] and new[None] == old[None]
    assert new['2026-03-09'] != old['2026-03-09']
    assert verify(warehouse)['ok']
    with connect_store(warehouse, 'history') as con:
        assert con.execute('SELECT count(*),sum(bid) FROM option_nbbo').fetchone() == (5,16)
    assert any(e['table'] == 'option_nbbo' and e['reused'] for e in events)


def test_queued_publication_waits_for_writer_without_exposing_partial_release(warehouse):
    import fcntl
    import threading
    import concurrent.futures
    first = publish(warehouse)
    entered = threading.Event()
    def queued():
        entered.set()
        return publish(warehouse, wait_for_writer=True)
    with (warehouse / 'var/maintenance/writer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(queued)
            assert entered.wait(2)
            assert not job.done()
            assert Release(warehouse).id == first['release']
            fcntl.flock(lock, fcntl.LOCK_UN)
            result = job.result(timeout=30)
    assert result['release'] != first['release']
    assert verify(warehouse)['ok']


def test_reader_spill_directories_are_private_and_cleaned():
    import gc
    from pathlib import Path
    from data_providers.lake import configured_connection

    first, second = configured_connection(), configured_connection()
    a = Path(first.execute("select current_setting('temp_directory')").fetchone()[0])
    b = Path(second.execute("select current_setting('temp_directory')").fetchone()[0])
    assert a != b and a.is_dir() and b.is_dir()
    # A connection's cleanup must never touch its peer's scratch blocks.
    (a / 'owned').write_text('first')
    (b / 'owned').write_text('second')
    first.close()
    del first
    gc.collect()
    assert not a.exists() and (b / 'owned').read_text() == 'second'
    second.close()
    del second
    gc.collect()
    assert not b.exists()


def test_daily_files_reused_across_legacy_yearly_release(warehouse, monkeypatch):
    from data_providers import lake
    with duckdb.connect(str(warehouse / STORES['history'])) as con:
        con.execute('CREATE TABLE option_nbbo(source VARCHAR, underlying VARCHAR, interval_ts TIMESTAMPTZ, bid DOUBLE)')
        con.execute("INSERT INTO option_nbbo VALUES ('theta','SPX','2026-01-02 15:00:00+00',1), ('theta','SPX','2026-01-03 15:00:00+00',2)")
    daily = publish(warehouse)
    old = {p['day']: p['path'] for p in daily['stores']['history']['tables']['option_nbbo']['partitions']}
    with monkeypatch.context() as patch:
        patch.setattr(lake, 'DAILY_OPTION_TABLES', frozenset())
        legacy = publish(warehouse)
    assert 'day' not in legacy['stores']['history']['tables']['option_nbbo']['partitions'][0]
    with duckdb.connect(str(warehouse / STORES['history'])) as con:
        con.execute('UPDATE option_nbbo SET bid=3 WHERE bid=2')
    restored = publish(warehouse)
    new = {p['day']: p['path'] for p in restored['stores']['history']['tables']['option_nbbo']['partitions']}
    assert new['2026-01-02'] == old['2026-01-02']
    assert new['2026-01-03'] != old['2026-01-03']
    assert verify(warehouse)['ok']


def test_coalescing_publisher_reuses_advanced_release_without_new_build(warehouse):
    first = publish(warehouse, private=True)
    other = publish(warehouse, private=True)
    before = set((warehouse / 'lake/snapshots').iterdir())
    events = []
    reused = publish(warehouse, private=True, wait_for_writer=True,
                     reuse_if_current_changed=first['release'], progress=events.append)
    assert reused['release'] == other['release']
    assert set((warehouse / 'lake/snapshots').iterdir()) == before
    assert events == [{'phase': 'existing_release_reused', 'release': other['release']}]
    assert verify(warehouse, reused['release'])['ok']


def test_coalescing_private_request_does_not_reuse_public_release(warehouse):
    first = publish(warehouse)
    other = publish(warehouse)
    result = publish(warehouse, private=True, reuse_if_current_changed=first['release'])
    assert result['release'] != other['release']
    assert 'trades' in result['stores']
