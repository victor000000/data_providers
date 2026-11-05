import os

import pytest

from data_providers.jobs.trades.scan_inputs import scan


def test_rescans_content_even_when_size_and_mtime_match(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    path = source / "kq_trades_20261019.csv"
    path.write_bytes(b"old")
    home = tmp_path / "runtime"
    first = scan(source, home)
    original = path.stat()
    path.write_bytes(b"new")
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    second = scan(source, home)
    assert second["changed"] == [path.name]
    assert first["files"][0]["sha256"] != second["files"][0]["sha256"]
    assert scan(source, home)["changed"] == []


def test_new_removed_nested_and_newest_modified_export(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    newer_date = source / "kq_trades_20261019.csv"
    newer_date.write_text("one")
    home = tmp_path / "runtime"
    scan(source, home)
    earlier_date = source / "kq_trades_20261018.csv"
    earlier_date.write_text("two")
    stamp = newer_date.stat().st_mtime_ns + 1000000
    os.utime(earlier_date, ns=(stamp, stamp))
    nested = source / "broker"
    nested.mkdir()
    (nested / "new.csv").write_text("three")
    (source / "~$journal.xlsx").write_text("lock")
    report = scan(source, home)
    assert set(report["added"]) == {earlier_date.name, "broker/new.csv"}
    assert report["newest_by_type"]["kq_trades.csv"]["path"] == earlier_date.name
    earlier_date.unlink()
    assert scan(source, home)["removed"] == [earlier_date.name]


def test_missing_mount_does_not_create_runtime(tmp_path):
    home = tmp_path / "runtime"
    with pytest.raises(FileNotFoundError):
        scan(tmp_path / "missing", home)
    assert not home.exists()
