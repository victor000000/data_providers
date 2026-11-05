"""Immutable Parquet releases, queried with DuckDB; ingestion stores remain private writers.

A release is visible only after validation and an atomic CURRENT replacement. Readers
pin one manifest, so a publication cannot mix versions within a client/session.
"""
import hashlib
import json
import os
import re
import tempfile
import uuid
import weakref
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import duckdb

from .catalog import CATALOG
from .provenance import source_descriptor

STORES = {
    "history": "market_history.duckdb",
    "universe": "market_data/yahoo.duckdb",
    "cn": "cn_market.duckdb",
    "reference": "reference.duckdb",
    "trades": "trades.duckdb",
}
VERSION = 1
DAILY_OPTION_TABLES = frozenset({"option_nbbo", "option_intraday_greeks"})


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Release path escapes the lake root")
    return path


def typed_query(reader, schema):
    # JSON and 128-bit integers use lossless text storage; restore the logical
    # contract explicitly, without altering any source values.
    projection = ",".join(f"CAST({ident(c['name'])} AS {c['type']}) AS {ident(c['name'])}" for c in schema)
    return f"SELECT {projection} FROM {reader}"


def configured_connection():
    con = duckdb.connect(":memory:")
    # DuckDB's default in-memory spill directory is shared by every reader in
    # the working directory. Concurrent connections can overwrite or remove
    # one another's blocks. Keep each connection's scratch files private.
    scratch = tempfile.TemporaryDirectory(prefix=f"data-providers-duckdb-{os.getpid()}-")
    try:
        con.execute("SET temp_directory=" + literal(scratch.name))
        con.execute("SET TimeZone='UTC'")
        con.execute("SET threads=2")
        con.execute("SET memory_limit='1GB'")
        con.execute("SET preserve_insertion_order=false")
        weakref.finalize(con, scratch.cleanup)
    except Exception:
        con.close()
        scratch.cleanup()
        raise
    return con


class Release:
    def __init__(self, home, release=None):
        self.root = Path(home) / "lake"
        if release is None:
            release = (self.root / "CURRENT").read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", release):
            raise ValueError("Invalid release identifier")
        self.id = release
        self.manifest = json.loads((self.root / "snapshots" / release / "manifest.json").read_text())
        if self.manifest.get("version") != VERSION or self.manifest.get("release") != release:
            raise ValueError("Unsupported or inconsistent release manifest")

    def table(self, store, table):
        try:
            return self.manifest["stores"][store]["tables"][table]
        except KeyError as exc:
            raise FileNotFoundError(f"{store}.{table} is not published in release {self.id}") from exc

    def query(self, store, table):
        entry = self.table(store, table)
        paths = [str(safe_path(self.root, p["path"])) for p in entry["partitions"]]
        # Exact paths, never a glob: incomplete/orphaned files cannot enter a query.
        return typed_query(f"read_parquet([{','.join(map(literal, paths))}], hive_partitioning=false)", entry["schema"])

    def attach(self, con, store, alias=None):
        if alias:
            con.execute(f"ATTACH ':memory:' AS {ident(alias)}")
        prefix = ident(alias) + "." if alias else ""
        for table in self.manifest["stores"][store]["tables"]:
            con.execute(f"CREATE VIEW {prefix}{ident(table)} AS {self.query(store, table)}")


def current(home):
    """Missing release is the only fallback condition; corrupt releases fail loudly."""
    if not (Path(home) / "lake" / "CURRENT").is_file():
        return None
    return Release(home)


def fingerprint(con, query, columns):
    hashed = "hash(" + ",".join(map(ident, columns)) + ")"
    row = con.execute(f"SELECT count(*), bit_xor({hashed}), sum({hashed}::HUGEINT) FROM ({query})").fetchone()
    return [str(value) if value is not None else None for value in row]


def exact_mismatch(con, left, right, *, time_col=None, rows=0):
    """Compare multisets exactly, bounding large temporal joins to a UTC day.

    Include dates from both sides and the NULL bucket, so extra rows and changed
    duplicate multiplicity cannot disappear at slice boundaries.
    """
    predicates = [""]
    if time_col and rows > 250_000:
        column = ident(time_col)
        days = con.execute(
            f"SELECT DISTINCT CAST({column} AS DATE) FROM ({left}) "
            f"UNION SELECT DISTINCT CAST({column} AS DATE) FROM ({right})"
        ).fetchall()
        predicates = [f" WHERE {column} IS NULL" if day is None else
                      f" WHERE {column}>=DATE {literal(str(day))} AND "
                      f"{column}<DATE {literal(str(day + timedelta(days=1)))}"
                      for (day,) in days]
    for predicate in predicates:
        a = f"SELECT * FROM ({left}){predicate}"
        b = f"SELECT * FROM ({right}){predicate}"
        for first, second in ((a, b), (b, a)):
            if con.execute(f"SELECT EXISTS(SELECT * FROM (({first}) EXCEPT ALL ({second})))").fetchone()[0]:
                return True
    return False


def publish(home, *, private=False, allow_schema_change=False, wait_for_writer=False,
            progress=None, reuse_if_current_changed=None):
    """Publish all available stores after writers finish. Failed builds remain resumable evidence.

    A per-store read transaction pins source contents. All store handles are acquired
    before copying anything; active DuckDB writers cause failure, never a partial release.
    Unchanged partitions reuse immutable files from earlier releases. Option quote
    and intraday Greek tables use UTC days so an arriving session does not rewrite
    a whole year. Explicitly queued publishers can wait on the existing writer lock.
    A coalescing caller may offer a prior release ID: if CURRENT advances while it
    waits, return that existing manifest for verification and selected inclusion
    checks instead of creating another snapshot. Default publication is unchanged.
    """
    import fcntl
    home = Path(home).resolve()
    root = home / "lake"
    root.mkdir(parents=True, exist_ok=True)
    maintenance = home / "var" / "maintenance"
    maintenance.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        # Same lock as the maintenance supervisor, then the release lock for other publishers.
        for lockfile in (maintenance / "writer.lock", root / "publish.lock"):
            lock = stack.enter_context(lockfile.open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX |
                            (0 if wait_for_writer else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise RuntimeError("Maintenance or publication is active; publish after it finishes") from exc
        previous = current(home)
        if (previous and reuse_if_current_changed is not None
                and previous.id != reuse_if_current_changed
                and (not private or "trades" in previous.manifest["stores"])):
            if progress:
                progress({"phase": "existing_release_reused", "release": previous.id})
            return previous.manifest
        ancestors = {previous.id: previous} if previous else {}
        release = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
        folder = root / "snapshots" / release
        pending = root / "snapshots" / (".incomplete-" + release)
        pending.mkdir(parents=True)
        manifest = {"version": VERSION, "release": release, "created_utc": datetime.now(UTC).isoformat(),
                    "parent": previous.id if previous else None, "duckdb_version": duckdb.__version__, "stores": {},
                    "datasets": {name: {"store": next(k for k,v in STORES.items() if v == spec.file),
                                         "table": spec.table, "time": spec.time, "symbol": spec.symbol,
                                         "private": spec.private}
                                 for name, spec in CATALOG.items() if private or not spec.private},
                    "conventions": {"timestamps": "UTC", "prices": "raw OHLC; adjusted values remain adj_*",
                                    "cn_identity": "venue-qualified; source columns preserved"}}
        connections = {}
        try:
            for store, filename in STORES.items():
                if store == "trades" and not private:
                    continue
                path = home / filename
                if not path.is_file():
                    raise FileNotFoundError(f"Missing ingestion store: {path}")
                con = stack.enter_context(duckdb.connect(str(path), read_only=True))
                con.execute("SET TimeZone='UTC'")
                con.execute("SET threads=2")
                con.execute("SET memory_limit='1GB'")
                con.execute("SET preserve_insertion_order=false")
                con.execute("BEGIN TRANSACTION")
                connections[store] = con
            for store, con in connections.items():
                tables = con.execute("SELECT table_name FROM information_schema.tables "
                                     "WHERE table_schema='main' ORDER BY table_name").fetchall()
                if previous and store in previous.manifest["stores"] and not allow_schema_change:
                    removed = set(previous.manifest["stores"][store]["tables"]) - {row[0] for row in tables}
                    if removed:
                        raise ValueError(f"Removed tables in {store}: {sorted(removed)}; explicit schema migration required")
                record = {"source": STORES[store], "private": store == "trades", "tables": {}}
                manifest["stores"][store] = record
                for (table,) in tables:
                    schema = [{"name": r[0], "type": r[1]} for r in con.execute(f"DESCRIBE {ident(table)}").fetchall()]
                    columns = [r["name"] for r in schema]
                    spec = next((s for s in CATALOG.values() if s.file == STORES[store] and s.table == table), None)
                    time_col = spec.time if spec else next((c for c in ("day", "date", "ts_utc") if c in columns), None)
                    # Only temporal SQL types are partitionable; a provider string date stays lossless.
                    if time_col and not any(r["name"] == time_col and r["type"].startswith(("DATE", "TIMESTAMP")) for r in schema):
                        time_col = None
                    daily = bool(time_col and store == "history" and table in DAILY_OPTION_TABLES)
                    source_monthly = bool(time_col and table == "iv_observations_v2")
                    signatures = {}
                    if source_monthly:
                        hashed = "hash(" + ",".join(map(ident, columns)) + ")"
                        summaries = con.execute(
                            f"SELECT datasource_tag,date_trunc('month',{ident(time_col)})::DATE,"
                            f"count(*),bit_xor({hashed}),sum({hashed}::HUGEINT) "
                            f"FROM {ident(table)} GROUP BY 1,2 ORDER BY 1,2").fetchall()
                        for source_value, month, *values in summaries:
                            signatures[(source_value, month)] = [str(v) if v is not None else None for v in values]
                        slices = list(signatures) or [(None, None)]
                    elif daily:
                        # One source pass for every day's fingerprint, rather than
                        # rescanning a growing multi-year table for each session.
                        hashed = "hash(" + ",".join(map(ident, columns)) + ")"
                        summaries = con.execute(
                            f"SELECT CAST({ident(time_col)} AS DATE), count(*), "
                            f"bit_xor({hashed}), sum({hashed}::HUGEINT) "
                            f"FROM {ident(table)} GROUP BY 1 ORDER BY 1"
                        ).fetchall()
                        for day, *values in summaries:
                            signatures[day] = [str(v) if v is not None else None for v in values]
                        slices = [(day,) for day in signatures] or [(None,)]
                    elif time_col:
                        hashed = "hash(" + ",".join(map(ident, columns)) + ")"
                        summaries = con.execute(
                            f"SELECT year({ident(time_col)}),count(*),bit_xor({hashed}),"
                            f"sum({hashed}::HUGEINT) FROM {ident(table)} GROUP BY 1 ORDER BY 1").fetchall()
                        for year_value, *values in summaries:
                            signatures[year_value] = [str(v) if v is not None else None for v in values]
                        slices = [(year_value,) for year_value in signatures] or [(None,)]
                    else:
                        slices = [(None,)]
                        slices = slices or [(None,)]  # Empty datasets still publish typed files.
                    constraints = con.execute("SELECT constraint_column_names FROM duckdb_constraints() "
                                              "WHERE schema_name='main' AND table_name=? AND constraint_type='PRIMARY KEY'", [table]).fetchall()
                    entry = {"primary_key": constraints[0][0] if constraints else [], "schema": schema, "partition_column": time_col, "partitions": [], "rows": 0}
                    entry['datasource'] = source_descriptor(store, table, columns)
                    if daily:
                        entry["partition_granularity"] = "utc_day"
                    if source_monthly:
                        entry['partition_granularity'] = 'datasource_month'
                    previous_table = previous.manifest["stores"].get(store, {}).get("tables", {}).get(table) if previous else None
                    reuse_tables = [previous_table] if previous_table else []
                    if daily and previous_table and previous_table.get("partition_granularity") != "utc_day":
                        # A legacy process may have published yearly partitions after
                        # a daily release. Reuse that earlier immutable daily layout
                        # instead of copying and sorting unchanged history again.
                        cursor = previous
                        seen = set()
                        while cursor and cursor.id not in seen:
                            seen.add(cursor.id)
                            parent = cursor.manifest.get("parent")
                            if not parent:
                                break
                            if parent not in ancestors:
                                ancestors[parent] = Release(home, parent)
                            cursor = ancestors[parent]
                            candidate = cursor.manifest["stores"].get(store, {}).get("tables", {}).get(table)
                            if candidate and candidate.get("partition_granularity") == "utc_day" and candidate["schema"] == schema:
                                reuse_tables.append(candidate)
                                break
                    if previous_table and not allow_schema_change:
                        old_types = {c["name"]: c["type"] for c in previous_table["schema"]}
                        new_types = {c["name"]: c["type"] for c in schema}
                        if any(new_types.get(k) != v for k,v in old_types.items()) or previous_table["primary_key"] != entry["primary_key"]:
                            raise ValueError(f"Breaking schema change in {store}.{table}; migrate consumers before using --allow-schema-change")
                    if time_col:
                        bounds = con.execute(f"SELECT min({ident(time_col)}),max({ident(time_col)}) FROM {ident(table)}").fetchone()
                        entry["time_min"], entry["time_max"] = [str(v) if v is not None else None for v in bounds]
                    entry["schema_sha256"] = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
                    record["tables"][table] = entry
                    for slice_entry in slices:
                        source_value, slice_value = slice_entry if source_monthly else (None, slice_entry[0])
                        year = slice_value.year if (daily or source_monthly) and slice_value else slice_value
                        where = ""
                        if time_col:
                            if slice_value is None:
                                where = f" WHERE {ident(time_col)} IS NULL"
                            elif source_monthly:
                                stop = (slice_value.replace(day=28) + timedelta(days=4)).replace(day=1)
                                where = (f" WHERE {ident(time_col)}>=DATE {literal(slice_value)} AND "
                                         f"{ident(time_col)}<DATE {literal(stop)}")
                            elif daily:
                                where = (f" WHERE {ident(time_col)}>=DATE {literal(slice_value)} AND "
                                         f"{ident(time_col)}<DATE {literal(slice_value + timedelta(days=1))}")
                            else:
                                where = f" WHERE year({ident(time_col)})={int(year)}"
                        if source_monthly:
                            where += (" AND " if where else " WHERE ") + (
                                'datasource_tag IS NULL' if source_value is None else
                                'datasource_tag=' + literal(source_value))
                        query = f"SELECT * FROM {ident(table)}{where}"
                        signature_key = (source_value, slice_value) if source_monthly else slice_value
                        signature = signatures[signature_key] if signature_key in signatures else fingerprint(con, query, columns)
                        old = None
                        for old_table in reuse_tables:
                            if old_table.get("schema") == schema:
                                old = next((p for p in old_table["partitions"]
                                            if p["year"] == year and p["fingerprint"] == signature
                                            and ((p.get('datasource_tag') == source_value and
                                                  p.get('month') == (str(slice_value) if slice_value else None))
                                                 if source_monthly else
                                                 p.get("day") == (str(slice_value) if slice_value else None)
                                                 if daily else "day" not in p)), None)
                                if old:
                                    break
                        reused = bool(old and digest(safe_path(root, old["path"])) == old["sha256"])
                        partition_name = str(slice_value) if slice_value is not None else "unpartitioned"
                        if reused:
                            # An unchanged immutable Parquet file plus an equal row fingerprint
                            # establishes content equality. Re-running exact_mismatch here split
                            # multi-million-row unchanged partitions into one EXCEPT ALL per day,
                            # which turned every publication into an hours-long rescan.
                            partition = dict(old)
                        else:
                            relative = Path(store) / quote(table, safe="") / partition_name / "data.parquet"
                            if source_monthly:
                                relative = (Path(store) / quote(table, safe='') /
                                            ('datasource=' + quote(str(source_value), safe='')) /
                                            partition_name / 'data.parquet')
                            output = pending / relative
                            output.parent.mkdir(parents=True, exist_ok=True)
                            order = []
                            if spec and spec.symbol:
                                order.append(ident(spec.symbol))
                            if time_col:
                                order.append(ident(time_col))
                            # DuckDB writes HUGEINT as floating point in Parquet. Store its
                            # decimal text explicitly, then restore the manifest's SQL type.
                            physical = ",".join(
                                f"CAST({ident(c['name'])} AS VARCHAR) AS {ident(c['name'])}"
                                if c['type'] in {"HUGEINT", "UHUGEINT", "JSON"} else ident(c['name'])
                                for c in schema)
                            ordered = f"SELECT {physical} FROM ({query})" + (" ORDER BY " + ",".join(order) if order else "")
                            con.execute(f"COPY ({ordered}) TO {literal(output)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)")
                            reader = typed_query(f"read_parquet({literal(output)}, hive_partitioning=false)", schema)
                            copied_schema = [{"name": r[0], "type": r[1]} for r in con.execute(f"DESCRIBE ({reader})").fetchall()]
                            if schema != copied_schema or fingerprint(con, reader, columns) != signature:
                                raise ValueError(f"Parquet validation failed: {store}.{table}/{year}")
                            mismatch = exact_mismatch(con, query, reader, time_col=time_col, rows=int(signature[0]))
                            if mismatch:
                                raise ValueError(f"Exact row validation failed: {store}.{table}/{year}")
                            partition = {"year": year, "rows": int(signature[0]), "fingerprint": signature,
                                         "path": str(Path("snapshots") / release / relative),
                                         "bytes": output.stat().st_size, "sha256": digest(output)}
                            if daily:
                                partition["day"] = str(slice_value) if slice_value else None
                            if source_monthly:
                                partition.update(datasource_tag=source_value,
                                                 month=str(slice_value) if slice_value else None)
                        entry["partitions"].append(partition)
                        entry["rows"] += partition["rows"]
                        if progress:
                            progress({"phase": "partition_validated", "release": release,
                                      "store": store, "table": table, "partition": partition_name,
                                      "rows": partition["rows"], "reused": reused})
                    print(f"published {store}.{table}: {entry['rows']} rows", flush=True)
            (pending / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            os.rename(pending, folder)
            # fsync the completed files and manifest before exposing the pointer.
            for path in folder.rglob("*"):
                if path.is_file():
                    with path.open("rb") as handle:
                        os.fsync(handle.fileno())
            directories = [p for p in folder.rglob("*") if p.is_dir()] + [folder, root / "snapshots"]
            for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            pointer = root / (".CURRENT-" + release)
            with pointer.open("w") as handle:
                handle.write(release + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pointer, root / "CURRENT")
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return manifest
        except Exception as exc:
            if pending.exists():
                (pending / "failure.json").write_text(json.dumps({"error": str(exc), "release": release}) + "\n")
            raise


def verify(home, release=None):
    snapshot = Release(home, release)
    con = configured_connection()
    try:
        checked = 0
        for store, metadata in snapshot.manifest["stores"].items():
            for table, entry in metadata["tables"].items():
                if entry["rows"] != sum(p["rows"] for p in entry["partitions"]):
                    raise ValueError(f"Invalid row count: {store}.{table}")
                schema_hash = hashlib.sha256(json.dumps(entry["schema"], sort_keys=True).encode()).hexdigest()
                if schema_hash != entry.get("schema_sha256", schema_hash):
                    raise ValueError(f"Invalid schema checksum: {store}.{table}")
                for partition in entry["partitions"]:
                    path = safe_path(snapshot.root, partition["path"])
                    if digest(path) != partition["sha256"]:
                        raise ValueError(f"Checksum mismatch: {store}.{table}: {path}")
                    query = typed_query(f"read_parquet({literal(path)}, hive_partitioning=false)", entry["schema"])
                    schema = [{"name": r[0], "type": r[1]} for r in con.execute(f"DESCRIBE ({query})").fetchall()]
                    if schema != entry["schema"] or fingerprint(con, query, [r['name'] for r in schema]) != partition["fingerprint"]:
                        raise ValueError(f"Content mismatch: {store}.{table}: {path}")
                    checked += 1
        return {"release": snapshot.id, "partitions_verified": checked, "ok": True}
    finally:
        con.close()


def connect_store(home, store, *, backend="auto", release=None):
    """Read-only delivery connection for an entire logical store, including legacy SQL."""
    if store not in STORES:
        raise ValueError(f"Unknown store: {store}")
    if backend not in {"auto", "parquet", "duckdb"}:
        raise ValueError("backend must be auto, parquet or duckdb")
    snapshot = (Release(home, release) if release else current(home)) if backend != "duckdb" else None
    if snapshot:
        con = configured_connection()
        try:
            snapshot.attach(con, store)
            return con
        except Exception:
            con.close()
            raise
    if backend == "parquet":
        raise FileNotFoundError("No Parquet release published")
    path = Path(home) / STORES[store]
    if not path.is_file():
        raise FileNotFoundError(path)
    con = duckdb.connect(str(path), read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con
