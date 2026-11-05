import json
from datetime import date

import duckdb
import pytest

from data_providers.jobs.audit.brain_inventory import build, classification, extend_fields, option_values
from data_providers.lake import STORES, digest, publish


def field(key, description="Daily close price", types=("MATRIX",)):
    category, dataset, name = key.split(":")
    return {"field_key": key, "category_id": category, "dataset_id": dataset, "field_id": name,
                "descriptions_json": json.dumps([description]), "types_json": json.dumps(types)}


def test_definition_identity_and_vector_alignment_are_not_guessed():
    assert classification(field("pv:pv1:close"))["dataset"] == "us_prices"
    assert classification(field("pv:other:close"))["dataset"] is None
    assert classification(field("pv:pv1:close", types=("VECTOR",)))["status"] == "ambiguous_definition"
    assert classification(field("option:option1:opt1_price_close_ini", types=("VECTOR",)))["dataset"] is None
    assert classification(field("option:option6:opt6_30div"))["status"] == "ambiguous_definition"
    assert classification(field("pv:pv1:vwap"))["dataset"] is None
    assert classification(field("pv:pv1:returns"))["dataset"] is None


def test_inventory_uses_pinned_raw_values_not_adjusted_or_live_store(tmp_path):
    home, catalog = tmp_path / "runtime", tmp_path / "catalog"
    catalog.mkdir()
    for store, filename in STORES.items():
        path = home / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(path)) as c:
            if store == "universe":
                c.execute("CREATE TABLE prices(symbol VARCHAR,date DATE,open DOUBLE,high DOUBLE,"
                          "low DOUBLE,close DOUBLE,volume BIGINT,adj_close DOUBLE)")
                c.execute("INSERT INTO prices VALUES ('SPY','2026-10-01',10,12,9,11,0,5.5)")
            else:
                c.execute("CREATE TABLE empty(value DOUBLE)")
    release = publish(home)["release"]
    with duckdb.connect(str(home / STORES["universe"])) as c:
        c.execute("UPDATE prices SET close=999")
    with duckdb.connect() as c:
        c.execute("CREATE TABLE fields(field_key VARCHAR,category_id VARCHAR,dataset_id VARCHAR,"
                  "field_id VARCHAR,descriptions_json JSON,types_json JSON)")
        for key in ["pv:pv1:close", "pv:pv1:volume", "pv:pv1:vwap", "option:option1:opt1_price_close_ini"]:
            c.execute("INSERT INTO fields VALUES (?,?,?,?,?,?)", list(field(key).values()))
        c.execute("COPY fields TO ? (FORMAT PARQUET)", [str(catalog / "fields.parquet")])
    (catalog / "manifest.json").write_text(json.dumps({
        "counts": {"fields": 4}, "sha256": {"fields.parquet": digest(catalog / "fields.parquet")}}))
    folder, report = build(catalog, home=home, release=release)
    assert report["sample_values"] == 2
    assert report["mapping_status_counts"] == {"observed": 2, "metadata_only": 2}
    assert not report["brain_value_equivalence_verified"]
    with duckdb.connect(str(folder / "inventory.duckdb"), read_only=True) as c:
        assert c.execute("SELECT value FROM sample_values WHERE field_key='pv:pv1:close'").fetchone() == (11,)
        assert c.execute("SELECT value FROM sample_values WHERE field_key='pv:pv1:volume'").fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM sample_values WHERE available_at IS NOT NULL").fetchone() == (0,)
    (catalog / "fields.parquet").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        build(catalog, home=home)


def test_no_missing_runtime_creation_and_bounded_window(tmp_path):
    with pytest.raises(ValueError, match="Sample"):
        build(tmp_path, home=tmp_path / "absent", start="2014-01-01")
    assert not (tmp_path / "absent").exists()


def test_fresh_union_keeps_unconfirmed_keys_and_both_description_sets(tmp_path):
    with duckdb.connect() as con:
        con.execute("CREATE TABLE fields(field_key VARCHAR,category_id VARCHAR,dataset_id VARCHAR,"
                    "field_id VARCHAR,descriptions_json JSON,types_json JSON)")
        for key in ("pv:pv1:close", "pv:other:cached_only"):
            con.execute("INSERT INTO fields VALUES (?,?,?,?,?,?)", list(field(key).values()))
        con.execute("CREATE TABLE supplement AS SELECT * FROM fields WHERE false")
        for key in ("pv:pv1:close", "pv:new:fresh_only"):
            con.execute("INSERT INTO supplement VALUES (?,?,?,?,?,?)",
                        list(field(key, description="Fresh scoped description").values()))
        con.execute("COPY supplement TO ? (FORMAT PARQUET)", [str(tmp_path / "fields.parquet")])
        con.execute("COPY (SELECT 'HKG' region, 'receipt.json' source_receipt) TO ? (FORMAT PARQUET)",
                    [str(tmp_path / "availability.parquet")])
        counts = extend_fields(con, tmp_path, {"counts": {"fresh_field_keys": 2}})
        assert counts == {"fresh_new": 1, "cached_unconfirmed_in_refreshed_scopes": 1,
                          "freshly_confirmed_in_refreshed_scopes": 1}
        assert con.execute("SELECT count(*) FROM fields").fetchone() == (3,)
        row = con.execute("SELECT descriptions_json,description_sets_differ FROM fields "
                          "WHERE field_key='pv:pv1:close'").fetchone()
        assert set(json.loads(row[0])) == {"Daily close price", "Fresh scoped description"}
        assert row[1]
        assert con.execute("SELECT region,source_receipt FROM fresh_availability").fetchone() == ("HKG", "receipt.json")
        names = [r[0] for r in con.execute("DESCRIBE fields").fetchall()]
        item = dict(zip(names, con.execute("SELECT * FROM fields WHERE field_key='pv:pv1:close'").fetchone()))
        assert classification(item)["status"] == "ambiguous_definition"


def test_option_vectors_preserve_contract_and_source_keys_without_aggregation():
    key = "option:option2:option_contract_trading_volume"
    base = {"source": "alpaca", "symbol": "SPY261016C00625000", "underlying": "SPY",
            "expiry": date(2026, 10, 16), "strike": 625.0, "call_put": "C",
            "day": date(2026, 10, 13), "volume": 8.0}
    rows = option_values([base, {**base, "symbol": "SPY261016C00626000", "strike": 626.0, "volume": 3.0},
                         {**base, "source": "other", "volume": 4.0}], {key}, "pinned")
    assert len(rows) == 3
    assert len({r[1] for r in rows}) == 3
    assert [r[8] for r in rows] == [8.0, 3.0, 4.0]
    assert all(r[11] is None for r in rows)  # No invented historical availability.
    with pytest.raises(ValueError, match="Fractional"):
        option_values([{**base, "volume": 1.5}], {key}, "pinned")
    with pytest.raises(ValueError, match="identity"):
        option_values([{**base, "symbol": None}], {key}, "pinned")
    assert classification(field(key, types=("MATRIX",)))["status"] == "ambiguous_definition"
