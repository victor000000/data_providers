"""Read-only SDK and atomic, reproducible exports for Python and other runtimes."""
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from .catalog import CATALOG
from .runtime import data_home


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


class MarketData:
    def __init__(self, home=None, *, private=False, backend="auto", release=None):
        self.home = Path(home).expanduser().resolve() if home else data_home()
        self.private = private
        self._backend = backend
        self._requested_release = release
        if backend not in {"auto", "parquet", "duckdb"}:
            raise ValueError("backend must be auto, parquet or duckdb")
        if backend == "duckdb" and release is not None:
            raise ValueError("An explicit release requires the Parquet backend")
        from .lake import Release, current
        self.release = (Release(self.home, release) if release else current(self.home)) if backend != "duckdb" else None
        if backend == "parquet" and self.release is None:
            raise FileNotFoundError("No Parquet release published")
        self.backend = "parquet" if self.release else "duckdb"

    def refresh(self):
        """Return a new client pinned to the latest release (or the explicit release)."""
        return type(self)(self.home, private=self.private, backend=self._backend, release=self._requested_release)

    def datasets(self):
        return [key for key, spec in CATALOG.items() if not spec.private or self.private]

    def _spec(self, dataset):
        if dataset not in CATALOG:
            raise ValueError(f"Unknown dataset: {dataset}")
        spec = CATALOG[dataset]
        if spec.private and not self.private:
            raise PermissionError("Trading data requires private=True")
        return spec

    @contextmanager
    def _connection(self, dataset):
        spec = self._spec(dataset)
        if self.release:
            from .lake import STORES, configured_connection
            store = next(name for name, filename in STORES.items() if filename == spec.file)
            con = configured_connection()
            try:
                con.execute(f"CREATE VIEW {identifier(spec.table)} AS {self.release.query(store, spec.table)}")
                yield con, spec
            finally:
                con.close()
            return
        path = self.home / spec.file
        if not path.is_file():
            raise FileNotFoundError(f"Dataset {dataset} is not initialized: {path}")
        con = duckdb.connect(str(path), read_only=True)
        try:
            con.execute("SET TimeZone='UTC'")
            yield con, spec
        finally:
            con.close()

    def readiness(self, contract):
        """Assess exact daily presence; see docs/READINESS.md for evidence limits."""
        from .readiness import assess
        return assess(self, contract)

    def status(self, dataset):
        spec = self._spec(dataset)
        if self.release:
            from .lake import STORES
            store = next(name for name, filename in STORES.items() if filename == spec.file)
            entry = self.release.table(store, spec.table)
            if not spec.time or "time_min" in entry:
                return {"dataset": dataset, "backend": self.backend, "release": self.release.id,
                        "rows": entry["rows"], "time_min": entry.get("time_min"),
                        "time_max": entry.get("time_max"), "time_column": spec.time}
        with self._connection(dataset) as (con, spec):
            bounds = f",min({identifier(spec.time)}),max({identifier(spec.time)})" if spec.time else ""
            row = con.execute(f"SELECT count(*){bounds} FROM {identifier(spec.table)}").fetchone()
            return {"dataset": dataset, "backend": self.backend,
                    "release": self.release.id if self.release else None,
                    "rows": row[0], "time_min": str(row[1]) if spec.time and row[1] is not None else None,
                    "time_max": str(row[2]) if spec.time and row[2] is not None else None, "time_column": spec.time}

    def schema(self, dataset):
        with self._connection(dataset) as (con, spec):
            return [{"name": row[0], "type": row[1]} for row in
                    con.execute(f"DESCRIBE {identifier(spec.table)}").fetchall()]

    def _select(self, con, spec, *, symbols=None, start=None, end=None, source=None,
                venue=None, columns=None, limit=None, contract_ids=None):
        descriptions = con.execute(f"DESCRIBE {identifier(spec.table)}").fetchall()
        available = [row[0] for row in descriptions]
        timestamp_columns = {row[0] for row in descriptions if row[1].startswith("TIMESTAMP")}
        selected = list(columns) if columns else available
        if not selected or set(selected) - set(available):
            raise ValueError("Unknown or empty column selection")
        where, params = [], []

        def predicate(column, operator, value):
            if column is None or column not in available:
                raise ValueError(f"This dataset does not support the {column!r} filter")
            where.append(f"{identifier(column)} {operator} ?")
            params.append(value)

        if symbols is not None:
            if isinstance(symbols, str):
                raise ValueError("symbols must be a list, not a string")
            if not symbols:
                where.append("false")
            else:
                if spec.symbol not in available:
                    raise ValueError("This dataset has no symbol filter")
                where.append(f"{identifier(spec.symbol)} IN ({','.join('?' for _ in symbols)})")
                params.extend(symbols)
        if contract_ids is not None:
            if isinstance(contract_ids, str):
                raise ValueError("contract_ids must be a list, not a string")
            if "contract_id" not in available:
                raise ValueError("This dataset has no contract_id filter")
            if not contract_ids:
                where.append("false")
            else:
                where.append(f"contract_id IN ({','.join('?' for _ in contract_ids)})")
                params.extend(contract_ids)
        end_operator = "<="
        if spec.time in timestamp_columns and end is not None:
            # A date-only upper bound includes the entire UTC session.
            try:
                day = date.fromisoformat(str(end))
            except ValueError:
                pass
            else:
                end, end_operator = day + timedelta(days=1), "<"
        for column, op, value in [(spec.time, ">=", start), (spec.time, end_operator, end),
                                  ("source", "=", source), ("venue", "=", venue)]:
            if value is not None:
                predicate(column, op, value)
        query = f"SELECT {','.join(map(identifier, selected))} FROM {identifier(spec.table)}"
        if where:
            query += " WHERE " + " AND ".join(where)
        # Total ordering makes repeated exports and pagination-free bounded reads reproducible.
        query += " ORDER BY ALL"
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError("limit must be a positive integer")
            query += " LIMIT ?"
            params.append(limit)
        return query, params

    def read(self, dataset, *, limit=10000, **filters):
        if limit is None or limit > 100000:
            raise ValueError("read limit must be <=100000; use export for larger datasets")
        with self._connection(dataset) as (con, spec):
            query, params = self._select(con, spec, limit=limit, **filters)
            cursor = con.execute(query, params)
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def read_tagged(self, dataset, *, limit=10000, **filters):
        """Traceable rows for every dataset; legacy unknowns stay explicit.

        Original read/schema contracts remain available. Projection happens after
        source attribution so requesting only price/date cannot erase its tag.
        """
        from .lake import STORES
        from .provenance import source_descriptor, tag_row, explicit_contract_receipts
        projected = filters.pop('columns', None)
        spec = self._spec(dataset)
        store = next(k for k, v in STORES.items() if v == spec.file)
        rows = self.read(dataset, limit=limit, **filters)
        with self._connection(dataset) as (con, _):
            columns = [r[0] for r in con.execute(f'DESCRIBE {identifier(spec.table)}').fetchall()]
            if self.release and spec.table in {'us_futures_daily', 'us_futures_option_settlements'}:
                try:
                    receipt_query = self.release.query(store, 'source_receipts')
                except FileNotFoundError:
                    pass  # Older releases retain explicitly unknown receipt linkage.
                else:
                    con.execute(f'CREATE VIEW source_receipts AS {receipt_query}')
            memberships = explicit_contract_receipts(con, spec.table, rows)
        if projected and any(name not in columns for name in projected):
            raise ValueError('Unknown projected column')
        descriptor = source_descriptor(store, spec.table, columns)
        tagged = [tag_row(row, descriptor=descriptor,
                          release=self.release.id if self.release else None) for row in rows]
        for row in tagged:
            receipt = memberships.get((row.get('source'), str(row.get('day')),
                                       row.get('contract_id')))
            if receipt and row['source_receipt_reference'] is None:
                row.update(source_receipt_id=receipt, source_receipt_reference=receipt,
                           source_receipt_reference_column='source_receipts.native_parameters.contract_ids',
                           provenance_status='explicit_receipt_membership_linked')
        if projected:
            metadata = {'datasource_tag', 'provenance_status', 'source_receipt_id',
                        'source_receipt_reference', 'source_receipt_reference_column',
                        'storage_release', 'consistency_tag', 'comparison_status'}
            tagged = [{k: v for k, v in row.items() if k in set(projected) | metadata}
                      for row in tagged]
        return tagged

    def export(self, dataset, destination, *, format="parquet", **filters):
        if format not in {"parquet", "csv"}:
            raise ValueError("format must be parquet or csv")
        destination = Path(destination).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A published directory contains both data and manifest. Rename once, so readers
        # cannot pair data from a new export with metadata from the previous export.
        if destination.exists():
            raise FileExistsError(f"Export destination already exists: {destination}")
        import shutil
        temporary = Path(tempfile.mkdtemp(prefix=".export-", dir=destination.parent))
        try:
            with self._connection(dataset) as (con, spec):
                query, params = self._select(con, spec, **filters)
                # Relations bind selection parameters before COPY; paths are SQL string literals.
                relation = con.sql(query, params=params)
                output = temporary / f"data.{format}"
                schema = [{"name": name, "type": str(dtype)} for name, dtype in
                          zip(relation.columns, relation.types)]
                if format == "parquet":
                    # Parquet has no 128-bit integer physical type. DuckDB's
                    # default DOUBLE conversion silently rounds IDs and totals.
                    projection = ",".join(
                        f"CAST({identifier(c['name'])} AS VARCHAR) AS {identifier(c['name'])}"
                        if c['type'] in {"HUGEINT", "UHUGEINT"} else identifier(c['name'])
                        for c in schema)
                    relation.project(projection).write_parquet(str(output), compression="zstd")
                else:
                    relation.write_csv(str(output), header=True)
                reader = "read_parquet" if format == "parquet" else "read_csv_auto"
                count = con.execute(f"SELECT count(*) FROM {reader}(?)", [str(output)]).fetchone()[0]
                physical_schema = [{"name": r[0], "type": r[1]} for r in
                                   con.execute(f"DESCRIBE SELECT * FROM {reader}(?)", [str(output)]).fetchall()]
            with output.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            manifest = {"contract_version": 1, "dataset": dataset, "format": format, "rows": count,
                        "sha256": digest, "created_utc": datetime.now(timezone.utc).isoformat(),
                        "filters": filters, "schema": schema, "physical_schema": physical_schema,
                        "release": self.release.id if self.release else None, "backend": self.backend,
                        "price_convention": "us_prices includes raw OHLC and adjusted adj_* columns"}
            (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n")
            # mkdtemp is 0700. Public market-data exports must be traversable
            # by read-only consumers running under another local account.
            # Trading-data exports retain their private directory permissions.
            temporary.chmod(0o700 if spec.private else 0o755)
            os.rename(temporary, destination)
            return manifest
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
