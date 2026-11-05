import json
from datetime import date, timedelta

import pytest

from data_providers.jobs.history.maintain_catalogs import run_window


def test_resume_keeps_window_across_utc_midnight(tmp_path):
    first = run_window(tmp_path, today=date(2026, 10, 17))
    assert first == (date(2026, 10, 16) - timedelta(days=729), date(2026, 10, 16))
    assert run_window(tmp_path, today=date(2026, 10, 20)) == first


def test_legacy_run_recovers_target_from_receipts(tmp_path):
    (tmp_path / 'prices.json').write_text(json.dumps({'CME:ES': '2026-10-16'}))
    assert run_window(tmp_path, today=date(2026, 10, 20))[1] == date(2026, 10, 16)


def test_legacy_discovery_recovers_reference_date(tmp_path):
    (tmp_path / 'contracts.json').write_text(json.dumps({'CME:ES': {'date': '2026-10-16'}}))
    assert run_window(tmp_path, today=date(2026, 10, 20))[1] == date(2026, 10, 16)


def test_mixed_receipts_fail_without_rewriting_evidence(tmp_path):
    path = tmp_path / 'prices.json'
    original = json.dumps({'a': '2026-10-16', 'b': '2026-10-17'})
    path.write_text(original)
    with pytest.raises(ValueError, match='Mixed target'):
        run_window(tmp_path)
    assert path.read_text() == original
    assert not (tmp_path / 'window.json').exists()
def test_metadata_snapshot_failure_rolls_back_all_rows():
    import duckdb
    import pytest

    from data_providers.data.schema.futures import ensure
    from data_providers.jobs.history.maintain_catalogs import metadata_upsert
    with duckdb.connect() as con:
        ensure(con)
        row = ('massive','ES','CME','2026-10-16','E-mini','{}')
        with pytest.raises(duckdb.ConstraintException):
            metadata_upsert(con,'us_futures_products',[row,(None,*row[1:])])
        assert con.execute('SELECT count(*) FROM us_futures_products').fetchone() == (0,)
        metadata_upsert(con,'us_futures_products',[row])
        assert con.execute('SELECT count(*) FROM us_futures_products').fetchone() == (1,)


def test_priority_queue_preserves_completed_and_deferred_inventory():
    from copy import deepcopy

    from data_providers.jobs.history.maintain_catalogs import price_queue

    def item(root='ES', kind='single', first='2025-01-01', last='2026-12-18'):
        return {'product_code':root,'type':kind,'first_trade_date':first,'last_trade_date':last}
    contracts={'done':item(),'near':item(),'far':item(last='2029-12-18'),
               'combo':item(kind='combo'),'other':item(root='XYZ'),
               'future':item(first='2026-10-17'),'expired':item(last='2023-12-18')}
    original=deepcopy(contracts)
    done={'done':'2026-10-16'}
    queue,deferred=price_queue(contracts,done,date(2024,10,17),date(2026,10,16),{'ES'})
    assert [key for key,_ in queue]==['near','far']
    assert deferred=={'combo':'combo_deferred','other':'other_root_deferred',
                      'future':'first_trade_after_cutoff','expired':'expired_before_window'}
    assert contracts==original and done=={'done':'2026-10-16'}
    # A completed contract in a differently pinned window is not silently skipped.
    assert 'done' in dict(price_queue(contracts,done,date(2024,10,18),date(2026,10,17),{'ES'})[0])

