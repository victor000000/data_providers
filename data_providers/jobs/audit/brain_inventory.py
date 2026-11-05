"""Build a complete BRAIN mapping inventory and a bounded, source-native PV sample.

This is a reproducible delivery audit, not a claim of BRAIN value equivalence.
It only reads an immutable MarketData release and the supplied metadata bundle.
"""
import argparse
import json
import math
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from data_providers import MarketData
from data_providers.lake import connect_store, digest, literal
from data_providers.runtime import data_home

PV_COLUMNS = {name: name for name in ("open", "high", "low", "close", "volume")}
OPTION_COLUMNS = {"option_contract_strike_price": "strike", "option_contract_trading_volume": "volume"}


def classification(field):
    """Only allowlisted dataset-qualified scalar fields receive a source mapping."""
    result = {"status": "metadata_only", "dataset": None, "column": None, "units": None,
                  "convention": None, "method": None, "dependencies": [], "equivalence": "unverified",
                  "next_action": "Review exact descriptions, scopes, inputs and historical source availability"}
    if field["field_key"] in {f"pv:pv1:{name}" for name in PV_COLUMNS}:
        if json.loads(field["types_json"]) != ["MATRIX"] or len(json.loads(field["descriptions_json"])) != 1:
            result.update(status="ambiguous_definition", next_action="Resolve description/type variants")
        else:
            column = PV_COLUMNS[field["field_id"]]
            result.update(dataset="us_prices", column=column,
                          units="shares" if column == "volume" else "source_quote_currency",
                          convention="raw_unadjusted", method="source_native_daily_v1",
                          dependencies=["universe.prices." + column],
                          next_action="Validate BRAIN adjustment, session, currency and universe equivalence")
    elif field["field_key"] in {f"option:option2:{name}" for name in OPTION_COLUMNS}:
        if json.loads(field["types_json"]) != ["VECTOR"] or len(json.loads(field["descriptions_json"])) != 1:
            result.update(status="ambiguous_definition", next_action="Resolve description/type variants")
        else:
            column = OPTION_COLUMNS[field["field_id"]]
            result.update(dataset="us_option_bars", column=column,
                          units="contracts" if column == "volume" else "source_quote_currency",
                          convention="source_reported_contract_adjustment_unknown", method="source_native_option_daily_v1",
                          dependencies=["option_bars.symbol", "option_bars.day", "option_bars." + column],
                          next_action="Validate BRAIN scope and adjusted deliverables; preserve source-qualified vector keys")
    elif field["field_key"] == "option:option6:opt6_30div":
        result.update(status="ambiguous_definition",
                      next_action="Resolve 30-day identifier versus saved 20-day description")
    elif field["field_key"] == "option:option1:opt1_price_close_ini":
        result.update(dependencies=["raw underlying stock close", "stable historical option vector keys"],
                      next_action="Align underlying stock close to option records; never interpret as option premium")
    elif field["field_key"] == "pv:pv1:returns":
        result.update(dependencies=["consecutive source prices", "corporate actions", "return convention"],
                      next_action="Resolve price versus total return and adjustment conventions before calculation")
    elif field["field_key"] == "pv:pv1:vwap":
        result.update(dependencies=["session trades or source-reported VWAP"],
                      next_action="Acquire actual VWAP inputs; OHLC averages are not VWAP")
    return result


def verify_catalog(catalog):
    catalog = Path(catalog).resolve()
    manifest = json.loads((catalog / "manifest.json").read_text())
    for name, expected in manifest["sha256"].items():
        path = (catalog / name).resolve()
        if not path.is_relative_to(catalog) or digest(path) != expected:
            raise ValueError("Catalog checksum mismatch or unsafe path: " + name)
    return catalog, manifest


def extend_fields(con, fresh_catalog, manifest):
    """Retain both definition sets; absent scoped keys are never deleted."""
    con.execute("ALTER TABLE fields RENAME TO cached_fields")
    con.execute("CREATE TABLE fresh_fields AS SELECT * FROM read_parquet(?)",
                [str(fresh_catalog / "fields.parquet")])
    rows, keys = con.execute("SELECT count(*),count(DISTINCT field_key) FROM fresh_fields").fetchone()
    if rows != keys or rows != manifest["counts"]["fresh_field_keys"]:
        raise ValueError("Incomplete or duplicate fresh field-key inventory")
    con.execute("""
        CREATE TABLE fields AS
        SELECT coalesce(c.field_key,f.field_key) field_key,
               coalesce(c.category_id,f.category_id) category_id,
               coalesce(c.dataset_id,f.dataset_id) dataset_id,
               coalesce(c.field_id,f.field_id) field_id,
               to_json(list_sort(list_distinct(list_concat(
                   from_json(coalesce(c.descriptions_json,'[]'),'["VARCHAR"]'),
                   from_json(coalesce(f.descriptions_json,'[]'),'["VARCHAR"]'))))) descriptions_json,
               to_json(list_sort(list_distinct(list_concat(
                   from_json(coalesce(c.types_json,'[]'),'["VARCHAR"]'),
                   from_json(coalesce(f.types_json,'[]'),'["VARCHAR"]'))))) types_json,
               c.descriptions_json cached_descriptions_json, f.descriptions_json fresh_descriptions_json,
               CASE WHEN c.field_key IS NULL THEN 'fresh_new'
                    WHEN f.field_key IS NULL THEN 'cached_unconfirmed_in_refreshed_scopes'
                    ELSE 'freshly_confirmed_in_refreshed_scopes' END metadata_status,
               c.field_key IS NOT NULL AND f.field_key IS NOT NULL AND
                   list_sort(from_json(c.descriptions_json,'["VARCHAR"]')) <>
                   list_sort(from_json(f.descriptions_json,'["VARCHAR"]')) description_sets_differ
        FROM cached_fields c FULL OUTER JOIN fresh_fields f USING(field_key)
    """)
    # Raw scoped rows keep independent retrieval times and original response references.
    con.execute("CREATE TABLE fresh_availability AS SELECT * FROM read_parquet(?)",
                [str(fresh_catalog / "availability.parquet")])
    return dict(con.execute("SELECT metadata_status,count(*) FROM fields GROUP BY 1").fetchall())


def option_values(rows, mapped_keys, release):
    result = []
    for row in rows:
        if not row["source"] or not row["symbol"] or not row["underlying"]:
            raise ValueError("Missing stable option vector identity")
        for field, column in OPTION_COLUMNS.items():
            key = "option:option2:" + field
            if key not in mapped_keys or row[column] is None:
                continue
            value = row[column]
            if not math.isfinite(value) or value < 0 or (column == "strike" and value == 0):
                raise ValueError("Invalid option sample value")
            if column == "volume" and value != int(value):
                raise ValueError("Fractional contract volume requires source investigation")
            result.append((key, row["source"] + ":" + row["symbol"], row["symbol"], row["underlying"],
                           row["expiry"], row["strike"], row["call_put"], row["day"], value,
                           "contracts" if column == "volume" else "source_quote_currency", row["source"],
                           None, None, release, "source_native_option_daily_v1",
                           "brain_equivalence_unverified;contract_adjustment_unknown;availability_unknown"))
    return result


def build(catalog, *, home=None, symbols=("SPY",), start="2026-10-01", end="2026-10-16", release=None,
          fresh_catalog=None, with_option_sample=False):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    symbols = tuple(dict.fromkeys(symbols))
    if start > end or (end - start).days > 31 or not 1 <= len(symbols) <= 10:
        raise ValueError("Sample must use 1–10 symbols and at most 32 calendar dates")
    catalog, manifest = verify_catalog(catalog)
    fresh_manifest = None
    if fresh_catalog is not None:
        fresh_catalog, fresh_manifest = verify_catalog(fresh_catalog)
    m = MarketData(home=home, backend="parquet", release=release)
    stamp = datetime.now(UTC)
    folder = m.home / "var/maintenance" / (stamp.strftime("brain-inventory-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    # Retain this folder on failure. Only report.json signifies verified completion.
    con = duckdb.connect(str(folder / "inventory.duckdb"))
    try:
        con.execute("SET TimeZone='UTC'")
        con.execute("BEGIN TRANSACTION")
        con.execute("CREATE TABLE fields AS SELECT * FROM read_parquet(?)", [str(catalog / "fields.parquet")])
        count, keys = con.execute("SELECT count(*),count(DISTINCT field_key) FROM fields").fetchone()
        if count != manifest["counts"]["fields"] or keys != count:
            raise ValueError("Incomplete or duplicate field-key inventory")
        freshness_counts = None
        tables = ["fields", "mapping", "sample_values"]
        if fresh_catalog is not None:
            freshness_counts = extend_fields(con, fresh_catalog, fresh_manifest)
            count = con.execute("SELECT count(*) FROM fields").fetchone()[0]
            tables.extend(["cached_fields", "fresh_fields", "fresh_availability"])
        con.execute("CREATE TABLE mapping (field_key VARCHAR PRIMARY KEY,status VARCHAR,dataset VARCHAR,"
                    "source_column VARCHAR,units VARCHAR,price_convention VARCHAR,method_version VARCHAR,"
                    "dependencies_json JSON,equivalence VARCHAR,next_action VARCHAR)")
        names = [x[0] for x in con.execute("DESCRIBE fields").fetchall()]
        records = []
        for row in con.execute("SELECT * FROM fields ORDER BY field_key").fetchall():
            field = dict(zip(names, row))
            item = classification(field)
            records.append((field["field_key"], item["status"], item["dataset"], item["column"], item["units"],
                            item["convention"], item["method"], json.dumps(item["dependencies"]),
                            item["equivalence"], item["next_action"]))
        con.executemany("INSERT INTO mapping VALUES (?,?,?,?,?,?,?,?,?,?)", records)
        mapped_keys = {r[0] for r in con.execute("SELECT field_key FROM mapping WHERE dataset='us_prices'").fetchall()}
        con.execute("CREATE TABLE sample_values (field_key VARCHAR,symbol VARCHAR,session_date DATE,"
                    "value DOUBLE,units VARCHAR,source VARCHAR,dataset VARCHAR,price_convention VARCHAR,"
                    "available_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ,method_version VARCHAR,"
                    "source_release VARCHAR,quality VARCHAR)")
        # Read source-native observations through the actual immutable SDK dataset.
        rows = m.read("us_prices", symbols=list(symbols), start=start, end=end)
        values = []
        for row in rows:
            for field_id, column in PV_COLUMNS.items():
                if f"pv:pv1:{field_id}" not in mapped_keys:
                    continue
                value = row[column]
                if value is None:
                    continue
                if not math.isfinite(value) or value < 0 or (column != "volume" and value == 0):
                    raise ValueError("Invalid source-native sample value")
                values.append((f"pv:pv1:{field_id}", row["symbol"], row["date"], value,
                               "shares" if column == "volume" else "source_quote_currency", "yahoo",
                               "us_prices", "raw_unadjusted", None, None, "source_native_daily_v1",
                               m.release.id, "source_native;brain_equivalence_unverified;availability_unknown"))
        if values:
            con.executemany("INSERT INTO sample_values VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        # A mapping is observed only when this exact bounded sample contains real values.
        con.execute("UPDATE mapping SET status='observed' WHERE dataset='us_prices' AND field_key IN "
                    "(SELECT DISTINCT field_key FROM sample_values)")
        duplicates = con.execute("SELECT count(*) FROM (SELECT field_key,symbol,session_date FROM sample_values "
                                 "GROUP BY ALL HAVING count(*)>1)").fetchone()[0]
        if duplicates:
            raise ValueError("Duplicate sample keys")
        with connect_store(m.home, "universe", release=m.release.id) as source:
            for row in rows:
                expected = source.execute("SELECT open,high,low,close,volume FROM prices WHERE symbol=? AND date=?",
                                          [row["symbol"], row["date"]]).fetchall()
                if len(expected) != 1 or tuple(row[c] for c in PV_COLUMNS) != expected[0]:
                    raise ValueError("Source equality verification failed")
        option_samples = []
        if with_option_sample:
            option_keys = {r[0] for r in con.execute(
                "SELECT field_key FROM mapping WHERE dataset='us_option_bars'").fetchall()}
            # Explicitly non-exhaustive: at most 100 actual source contract-day rows.
            native = m.read("us_option_bars", symbols=list(symbols), start=start, end=end, limit=100)
            option_samples = option_values(native, option_keys, m.release.id)
            con.execute("CREATE TABLE option_sample_values (field_key VARCHAR,contract_id VARCHAR,symbol VARCHAR,"
                        "underlying VARCHAR,expiry DATE,strike DOUBLE,call_put VARCHAR,session_date DATE,"
                        "value DOUBLE,units VARCHAR,source VARCHAR,available_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ,"
                        "source_release VARCHAR,method_version VARCHAR,quality VARCHAR,"
                        "PRIMARY KEY(field_key,contract_id,session_date))")
            if option_samples:
                con.executemany("INSERT INTO option_sample_values VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", option_samples)
            con.execute("CREATE TEMP VIEW source_option_bars AS " + m.release.query("history", "option_bars"))
            for field, column in OPTION_COLUMNS.items():
                if con.execute(f"SELECT count(*) FROM option_sample_values s WHERE field_key=? AND NOT EXISTS "
                               f"(SELECT 1 FROM source_option_bars b WHERE b.source=s.source AND b.symbol=s.symbol "
                               f"AND b.underlying=s.underlying AND b.expiry=s.expiry AND b.strike=s.strike "
                               f"AND b.call_put=s.call_put AND b.day=s.session_date AND b.{column}=s.value)",
                               ["option:option2:" + field]).fetchone()[0]:
                    raise ValueError("Option sample source equality verification failed")
            con.execute("UPDATE mapping SET status='observed' WHERE dataset='us_option_bars' AND field_key IN "
                        "(SELECT DISTINCT field_key FROM option_sample_values)")
            tables.append("option_sample_values")
        for table in tables:
            target = folder / (table + ".parquet")
            con.execute(f"COPY (SELECT * FROM {table} ORDER BY ALL) TO {literal(target)} (FORMAT PARQUET)")
            if con.execute(f"SELECT count(*) FROM read_parquet({literal(target)})").fetchone()[0] != con.execute(
                    f"SELECT count(*) FROM {table}").fetchone()[0]:
                raise ValueError("Parquet row verification failed")
            for left, right in ((table, f"read_parquet({literal(target)})"),
                                (f"read_parquet({literal(target)})", table)):
                if con.execute(f"SELECT count(*) FROM (SELECT * FROM {left} EXCEPT ALL SELECT * FROM {right})").fetchone()[0]:
                    raise ValueError("Parquet value verification failed")
        counts = dict(con.execute("SELECT status,count(*) FROM mapping GROUP BY status").fetchall())
        con.execute("COMMIT")
    finally:
        con.close()
    report = {"status": "verified_bounded_sample", "observed_utc": stamp.isoformat(), "source_release": m.release.id,
                  "catalog_manifest_sha256": digest(catalog / "manifest.json"), "catalog_path": str(catalog),
                  "catalog_metadata_retrieved_at": manifest.get("metadata_retrieved_at_utc"),
                  "fresh_catalog_path": str(fresh_catalog) if fresh_catalog else None,
                  "fresh_catalog_manifest_sha256": digest(fresh_catalog / "manifest.json") if fresh_catalog else None,
                  "metadata_freshness_counts": freshness_counts,
                  "field_count": count, "mapping_status_counts": counts, "sample_values": len(values),
                  "option_sample_values": len(option_samples), "option_sample_contract_day_limit": 100,
                  "option_source_partitions": m.release.table("history", "option_bars")["partitions"] if with_option_sample else None,
                  "sample_sessions": len(rows), "symbols": list(symbols), "start": str(start), "end": str(end),
                  "source_partitions": m.release.table("universe", "prices")["partitions"],
                  "brain_value_equivalence_verified": False, "historical_available_at_known": False,
                  "limitations": ["Five US PV primitives; optional two option contract primitives; remaining values pending",
                               "Source-qualified option vector records do not establish BRAIN vector universe equivalence",
                               "No constant-tenor IV, quote, HKG or proprietary model values claimed",
                               "Session dates are preserved; historical availability and retrieval time stay null",
                               "Sample copy does not refresh underlying provider data or publish a new lake release"],
                  "sha256": {p.name: digest(p) for p in folder.iterdir() if p.is_file()}}
    temporary = folder / "report.tmp"
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(folder / "report.json")
    return folder, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--symbols", nargs="+", default=["SPY"])
    parser.add_argument("--start", default="2026-10-01")
    parser.add_argument("--end", default="2026-10-16")
    parser.add_argument("--release")
    parser.add_argument("--fresh-catalog")
    parser.add_argument("--with-option-sample", action="store_true")
    args = parser.parse_args()
    folder, report = build(args.catalog, home=data_home(), symbols=args.symbols,
                           start=args.start, end=args.end, release=args.release, fresh_catalog=args.fresh_catalog,
                           with_option_sample=args.with_option_sample)
    print(json.dumps(dict(path=str(folder), **{k: report[k] for k in
                     ("status", "field_count", "mapping_status_counts", "sample_values", "option_sample_values", "source_release")})))


if __name__ == "__main__":
    main()
