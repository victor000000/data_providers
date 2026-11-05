"""Source-labelled observation contract; attribution is not value equivalence."""
import hashlib
import json

CONSISTENCY_TAG = 'cross_source_inconsistent'
COMPARISON_STATUS = 'not_compared'
IV_COLUMNS = frozenset({'iv', 'atm_iv', 'implied_vol', 'implied_volatility', 'iv_value'})

DDL = {
    'data_source_registry': '''CREATE TABLE IF NOT EXISTS data_source_registry (
        datasource_tag VARCHAR PRIMARY KEY, provider VARCHAR NOT NULL,
        feed VARCHAR NOT NULL, method_id VARCHAR NOT NULL,
        description VARCHAR, provenance_status VARCHAR NOT NULL)''',
    'source_receipts': '''CREATE TABLE IF NOT EXISTS source_receipts (
        receipt_id VARCHAR PRIMARY KEY, datasource_tag VARCHAR NOT NULL,
        provider VARCHAR NOT NULL, endpoint VARCHAR, native_parameters JSON,
        raw_path VARCHAR, raw_sha256 VARCHAR, original_receipt_path VARCHAR,
        original_receipt_sha256 VARCHAR, received_at TIMESTAMP,
        provenance_status VARCHAR NOT NULL)''',
    'iv_observations_v2': '''CREATE TABLE IF NOT EXISTS iv_observations_v2 (
        instrument_id VARCHAR NOT NULL, stored_underlying VARCHAR NOT NULL,
        market VARCHAR NOT NULL, venue VARCHAR, day DATE NOT NULL,
        source VARCHAR NOT NULL, datasource_tag VARCHAR NOT NULL,
        method_id VARCHAR NOT NULL, iv_value DOUBLE, underlying_price DOUBLE,
        tenor_days DOUBLE, expiry DATE, quote_time_utc TIMESTAMP,
        underlying_time_utc TIMESTAMP, available_at TIMESTAMP,
        receipt_id VARCHAR, raw_sha256 VARCHAR, ingested_at TIMESTAMP,
        provenance_status VARCHAR NOT NULL,
        consistency_tag VARCHAR NOT NULL DEFAULT 'cross_source_inconsistent',
        comparison_status VARCHAR NOT NULL DEFAULT 'not_compared',
        PRIMARY KEY(instrument_id,day,datasource_tag,method_id),
        CHECK(source=datasource_tag), CHECK(length(datasource_tag)>0),
        CHECK(comparison_status='not_compared'))''',
}


def ensure(con):
    """Explicit writer-only migration. Reading/importing never initializes stores."""
    for ddl in DDL.values():
        con.execute(ddl)


def source_descriptor(store, table, columns):
    column = next((name for name in ('datasource_tag', 'source', 'broker')
                   if name in columns), None)
    return {'tag_column': column,
            'fallback_tag': f'legacy_unverified:{store}.{table}',
            'receipt_column': next((name for name in ('receipt_id', 'receipt_sha256',
                                                      'source_receipt', 'receipt_reference',
                                                      'receipt_path', 'receipt', 'evidence_reference')
                                    if name in columns), None),
            'unattributed_legacy_status': 'legacy_unverified',
            'iv_consistency_tag': CONSISTENCY_TAG if IV_COLUMNS.intersection(columns) else None,
            'comparison_status': COMPARISON_STATUS}


def tag_row(row, *, descriptor, release=None):
    """Return a labelled copy, preserving every original native value."""
    result = dict(row)
    source = row.get('datasource_tag') or row.get(descriptor['tag_column'])
    result['datasource_tag'] = source or descriptor['fallback_tag']
    result['provenance_status'] = (row.get('provenance_status') or
                                   ('recorded_source_unlinked' if source else 'legacy_unverified'))
    result['source_receipt_id'] = row.get(descriptor['receipt_column'])
    result['source_receipt_reference'] = row.get(descriptor['receipt_column'])
    result['source_receipt_reference_column'] = descriptor['receipt_column']
    result['storage_release'] = release
    if descriptor['iv_consistency_tag']:
        result['consistency_tag'] = CONSISTENCY_TAG
        result['comparison_status'] = COMPARISON_STATUS
    return result


def explicit_contract_receipts(con, table, rows):
    """Resolve producer-declared settlement membership, never infer by date alone."""
    if table not in {'us_futures_daily', 'us_futures_option_settlements'} or not rows:
        return {}
    if ('source_receipts',) not in con.execute('SHOW TABLES').fetchall():
        return {}
    sources = sorted({r['source'] for r in rows if r.get('source')})
    days = sorted({str(r['day']) for r in rows if r.get('day')})
    if not sources or not days:
        return {}
    records = con.execute('''SELECT receipt_id,datasource_tag,native_parameters
        FROM source_receipts WHERE datasource_tag=ANY(?)
        AND json_extract_string(native_parameters,'$.dataset')=?
        AND json_extract_string(native_parameters,'$.day')=ANY(?)
        AND provenance_status='raw_checksum_verified'
        AND regexp_full_match(raw_sha256,'[0-9a-f]{64}')
        AND regexp_full_match(original_receipt_sha256,'[0-9a-f]{64}')
        AND length(raw_path)>0 AND length(original_receipt_path)>0''',
                          [sources, table, days]).fetchall()
    wanted = {(r.get('source'), str(r.get('day')), r.get('contract_id')) for r in rows}
    matches = {}
    for receipt_id, source, parameters in records:
        params = json.loads(parameters)
        ids = params.get('contract_ids')
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            continue
        for contract_id in ids:
            key = (source, params.get('day'), contract_id)
            if key in wanted:
                matches.setdefault(key, set()).add(receipt_id)
    # Multiple receipts are ambiguous, even when they share a date and source.
    return {key: next(iter(ids)) for key, ids in matches.items() if len(ids) == 1}


def receipt_envelope(*, datasource_tag, provider, receipt_path, raw_path=None,
                     endpoint=None, parameters=None, received_at=None):
    """Index immutable evidence without rewriting original receipts or raw bytes."""
    from pathlib import Path
    if not datasource_tag or not provider:
        raise ValueError('Datasource tag and provider are mandatory')
    receipt_path = Path(receipt_path)
    receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    raw_sha = hashlib.sha256(Path(raw_path).read_bytes()).hexdigest() if raw_path else None
    key = hashlib.sha256(json.dumps([datasource_tag, receipt_sha, raw_sha]).encode()).hexdigest()
    return dict(receipt_id=key, datasource_tag=datasource_tag, provider=provider,
                endpoint=endpoint, native_parameters=json.dumps(parameters or {}, sort_keys=True),
                raw_path=str(raw_path) if raw_path else None, raw_sha256=raw_sha,
                original_receipt_path=str(receipt_path), original_receipt_sha256=receipt_sha,
                received_at=received_at, provenance_status='raw_checksum_verified' if raw_sha
                else 'receipt_only_raw_unavailable')
