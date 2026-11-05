import hashlib
import json
import threading
from datetime import date
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import duckdb
import pytest

from data_providers import MarketData
from data_providers.server import create_server


@pytest.fixture
def warehouse(tmp_path):
    con = duckdb.connect(str(tmp_path / "market_history.duckdb"))
    con.execute("CREATE TABLE underlying_daily(day DATE, symbol TEXT, close DOUBLE, source TEXT)")
    con.execute("INSERT INTO underlying_daily VALUES ('2026-10-15','MA',100,'a'), "
                "('2026-10-16','MA',101,'a'), ('2026-10-16','MA',102,'b'), "
                "('2026-10-16','C',10,'a')")
    con.close()
    return MarketData(tmp_path)


def test_filters_keep_vendor_provenance_and_do_not_resolve_cn_collisions(warehouse):
    rows = warehouse.read("us_daily", symbols=["MA"], start="2026-10-16")
    assert {r["source"] for r in rows} == {"a", "b"}
    assert all(r["day"] == date(2026, 10, 16) for r in rows)
    assert warehouse.read("us_daily", symbols=["CN:CZCE:MA"]) == []
    assert warehouse.read("us_daily", symbols=[]) == []
    assert len(warehouse.read("us_daily", symbols=["MA"], source="a")) == 2


def test_untrusted_values_and_projection_are_validated(warehouse):
    assert warehouse.read("us_daily", symbols=["MA'); DROP TABLE underlying_daily;--"]) == []
    with pytest.raises(ValueError):
        warehouse.read("us_daily", columns=["close; DROP TABLE underlying_daily"])
    with pytest.raises(ValueError):
        warehouse.read("us_daily", venue="CZCE")
    for limit in [None, 0, -1, 100001, True]:
        with pytest.raises(ValueError):
            warehouse.read("us_daily", limit=limit)
    assert len(warehouse.read("us_daily")) == 4


@pytest.mark.parametrize("format", ["parquet", "csv"])
def test_atomic_exports_are_complete_and_reproducible(warehouse, tmp_path, format):
    destination = tmp_path / "export"
    manifest = warehouse.export("us_daily", destination, format=format, symbols=["MA"])
    assert manifest["rows"] == 3
    output = destination / f"data.{format}"
    assert hashlib.sha256(output.read_bytes()).hexdigest() == manifest["sha256"]
    assert json.loads((destination / "manifest.json").read_text()) == manifest
    assert manifest["schema"][0] == {"name": "day", "type": "DATE"}
    with pytest.raises(FileExistsError):
        warehouse.export("us_daily", destination)


def test_missing_dataset_never_creates_a_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        MarketData(tmp_path).read("us_daily")
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(PermissionError):
        MarketData(tmp_path).read("fills")
    assert "fills" not in MarketData(tmp_path).datasets()


def test_http_contract_auth_filters_and_private_access(warehouse):
    server = create_server(warehouse, port=0, token="test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def get(path, auth=True):
        request = Request(base + path, headers={"Authorization": "Bearer test-token"} if auth else {})
        return json.load(urlopen(request, timeout=5))
    try:
        assert "us_daily" in get("/v1/datasets")["datasets"]
        rows = get("/v1/data?dataset=us_daily&symbol=MA&source=b")["rows"]
        assert len(rows) == 1 and rows[0]["close"] == 102
        for url, code in [("/v1/datasets", 401), ("/v1/data?dataset=fills", 403),
                          ("/v1/data?dataset=us_daily&limit=100001", 400)]:
            with pytest.raises(HTTPError) as exc:
                get(url, auth=url != "/v1/datasets")
            assert exc.value.code == code
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_network_binding_requires_a_token(warehouse):
    with pytest.raises(ValueError):
        create_server(warehouse, host="0.0.0.0")


def test_minute_date_upper_bound_includes_the_full_utc_session(warehouse):
    with duckdb.connect(str(warehouse.home / "market_history.duckdb")) as con:
        con.execute("CREATE TABLE underlying_minutes(ts_utc TIMESTAMP,symbol TEXT,close DOUBLE,source TEXT)")
        con.execute("INSERT INTO underlying_minutes VALUES ('2026-10-16 23:59:00','MA',101,'test'),"
                    "('2026-10-17 00:00:00','MA',102,'test')")
    rows = warehouse.read("us_minutes", start="2026-10-16", end="2026-10-16")
    assert len(rows) == 1 and rows[0]["close"] == 101


def test_parquet_export_preserves_128_bit_values(warehouse, tmp_path):
    value = 2**100 + 123
    with duckdb.connect(str(warehouse.home / "market_history.duckdb")) as con:
        con.execute('ALTER TABLE underlying_daily ADD COLUMN exact_id HUGEINT')
        con.execute('ALTER TABLE underlying_daily ADD COLUMN unsigned_id UHUGEINT')
        con.execute('UPDATE underlying_daily SET exact_id=?, unsigned_id=?', [-value, value])
    destination = tmp_path / 'exact-export'
    manifest = warehouse.export('us_daily', destination, symbols=['MA'])
    with duckdb.connect() as con:
        rows = con.execute('SELECT exact_id, unsigned_id FROM read_parquet(?)',
                           [str(destination / 'data.parquet')]).fetchall()
    assert rows == [(str(-value), str(value))] * 3
    assert {'name': 'exact_id', 'type': 'HUGEINT'} in manifest['schema']
    assert {'name': 'exact_id', 'type': 'VARCHAR'} in manifest['physical_schema']


def test_public_export_can_be_read_by_other_local_consumers(warehouse,tmp_path):
    import os,stat
    if os.name=='nt':pytest.skip('POSIX directory traversal permissions')
    out=tmp_path/'shared-export'
    warehouse.export('us_daily',out)
    assert stat.S_IMODE(out.stat().st_mode)==0o755
