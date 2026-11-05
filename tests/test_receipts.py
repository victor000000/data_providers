import json
import os

import duckdb
import pytest

from data_providers.jobs.trades.capture_exports import capture


def test_capture_newest_per_type_is_lossless_and_idempotent(tmp_path):
    source, home = tmp_path / "exports", tmp_path / "warehouse"
    source.mkdir()
    old = source / "kq_trades_old.csv"
    old.write_bytes("成交合约,手数\nMA701,1\n".encode("gb18030"))
    os.utime(old, (1, 1))
    new = source / "kq_trades_new.csv"
    raw = "成交合约,手数\nMA701,2\n".encode("gb18030")
    new.write_bytes(raw)
    rows = capture(source, home)
    assert len(rows) == 1 and rows[0]["rows"] == 1
    assert capture(source, home) == []
    with duckdb.connect(str(home / "trades.duckdb"), read_only=True) as con:
        assert con.execute("SELECT raw_content FROM broker_exports").fetchone()[0] == raw
        payload = con.execute("SELECT payload FROM broker_export_rows").fetchone()[0]
        assert json.loads(payload)["手数"] == "2"


def test_bad_export_rolls_back_all_receipts(tmp_path):
    source, home = tmp_path / "exports", tmp_path / "warehouse"
    source.mkdir()
    (source / "kq_trades_1.csv").write_text("a,b\n1,2\n")
    (source / "yc_trades_1.csv").write_text("a,b\n1,2,3\n")
    with pytest.raises(ValueError, match="Malformed"):
        capture(source, home)
    with duckdb.connect(str(home / "trades.duckdb"), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM broker_exports").fetchone()[0] == 0
