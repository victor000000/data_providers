import hashlib
import os

from data_providers.jobs.trades.archive_exports import archive, export_kind


def test_newest_modified_type_keeps_original_bytes_and_provenance(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    old = source / '2026-10-17-PositionStatement.csv'
    new = source / '2026-10-16-PositionStatement.csv'
    old.write_bytes(b'old')
    new.write_bytes(b'\xfforiginal\r\n')
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new, ns=(2_000_000_000, 2_000_000_000))
    (source / '~$202610_tradelogs.xlsx').write_bytes(b'temporary lock')
    root, manifest = archive(source, tmp_path / 'home')
    assert len(manifest['files']) == 1
    row = manifest['files'][0]
    stored = root / row['path']
    assert row['filename'] == new.name
    assert stored.read_bytes() == new.read_bytes()
    assert stored.stat().st_mtime_ns == new.stat().st_mtime_ns
    assert row['sha256'] == hashlib.sha256(new.read_bytes()).hexdigest()
    assert row['modified_utc'] == '1970-01-01T00:00:02+00:00'
    assert (root / 'manifest.json').is_file()


def test_date_removal_preserves_non_date_account_identifiers():
    from pathlib import Path
    assert export_kind(Path('kq_trades_20261013.csv')) == 'kq_trades.csv'
    assert export_kind(Path('20101589_20261013.csv')) == '20101589.csv'
    assert export_kind(Path('OrderReport20261015.csv')) == 'orderreport.csv'
