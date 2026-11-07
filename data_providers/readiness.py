"""Read-only, source-isolated daily presence assessments."""
import hashlib
import json
import math
from datetime import UTC, date, datetime

from .client import identifier


def normalize(contract):
    if not isinstance(contract, dict) or set(contract) - {
        'version', 'dataset', 'symbols', 'sessions', 'filters', 'required_columns'
    }:
        raise ValueError('Invalid readiness contract fields')
    if type(contract.get('version')) is not int or contract['version'] != 1:
        raise ValueError('Readiness contract version must be 1')
    if not isinstance(contract.get('dataset'), str):
        raise TypeError('dataset must be a string')
    result = dict(contract)
    for key, maximum in [('symbols', 20000), ('sessions', 10000), ('required_columns', 100)]:
        values = contract.get(key, [] if key == 'required_columns' else None)
        if (not isinstance(values, list) or len(values) > maximum
                or (key != 'required_columns' and not values)
                or any(not isinstance(v, str) or not v for v in values)
                or len(set(values)) != len(values)):
            raise ValueError(f'{key} must be a bounded list of unique nonempty strings')
        result[key] = sorted(values)
    if len(result['symbols']) * len(result['sessions']) > 1000000:
        raise ValueError('Readiness contract exceeds one million symbol sessions')
    for value in result['sessions']:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('sessions must use YYYY-MM-DD')
    filters = contract.get('filters', {})
    if (not isinstance(filters, dict) or not filters or len(filters) > 100
            or not all(isinstance(k, str) and k for k in filters)
            or not ({'source', 'datasource_tag'} & filters.keys())):
        raise ValueError('filters must include source or datasource_tag')
    for value in filters.values():
        if (type(value) not in (str, int, float, bool)
                or (isinstance(value, str) and not value)
                or (type(value) is float and not math.isfinite(value))):
            raise ValueError('Filter values must be non-null finite scalars')
    result['filters'] = dict(sorted(filters.items()))
    return result


def assess(client, contract):
    contract = normalize(contract)
    encoded = json.dumps(contract, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    with client._connection(contract['dataset']) as (con, spec):
        columns = {row[0]: row[1] for row in con.execute(f'DESCRIBE {identifier(spec.table)}').fetchall()}
        requested = set(contract['filters']) | set(contract['required_columns'])
        if not spec.time or not spec.symbol or {spec.time, spec.symbol} - columns.keys():
            raise ValueError('Dataset requires catalog date and symbol columns')
        if requested - columns.keys():
            raise ValueError(f'Unknown readiness columns: {sorted(requested - columns.keys())}')
        if columns[spec.time] != 'DATE' and not columns[spec.time].startswith('TIMESTAMP'):
            raise ValueError('Readiness requires DATE or TIMESTAMP session values')
        con.execute("SET TimeZone='UTC'")
        day = f'CAST({identifier(spec.time)} AS DATE)'
        predicates, params = [], []
        for column, values in [(identifier(spec.symbol), contract['symbols']), (day, contract['sessions'])]:
            predicates.append(f"{column} IN ({','.join('?' for _ in values)})")
            params.extend(values)
        for column, value in contract['filters'].items():
            predicates.append(f'{identifier(column)} = ?')
            params.append(value)
        for column in contract['required_columns']:
            name = identifier(column)
            predicates.append(f'{name} IS NOT NULL')
            if columns[column].split('(')[0] in {
                'FLOAT', 'DOUBLE', 'DECIMAL', 'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT',
                'HUGEINT', 'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT', 'UHUGEINT'
            }:
                predicates.append(f'isfinite(CAST({name} AS DOUBLE))')
        rows = con.execute(
            f'SELECT {identifier(spec.symbol)}, {day}, count(*) FROM {identifier(spec.table)} '
            f"WHERE {' AND '.join(predicates)} GROUP BY 1,2 ORDER BY 1,2", params
        ).fetchall()
    observed = {symbol: {} for symbol in contract['symbols']}
    for symbol, session, count in rows:
        observed[symbol][session.isoformat()] = count
    results = []
    for symbol, counts in observed.items():
        missing = sorted(set(contract['sessions']) - counts.keys())
        results.append({'symbol': symbol, 'present_sessions': sorted(counts),
                        'missing_sessions': missing, 'qualifying_rows': sum(counts.values()),
                        'coverage_complete': not missing})
    complete = all(row['coverage_complete'] for row in results)
    published = client.release is not None
    return {'version': 1, 'contract': contract, 'contract_sha256': hashlib.sha256(encoded).hexdigest(),
            'assessed_at': datetime.now(UTC).isoformat(), 'backend': client.backend,
            'release': client.release.id if published else None,
            'status': ('coverage_complete' if complete else 'incomplete') if published else 'provisional',
            'coverage_complete': complete, 'ready': complete and published,
            'qualification': 'daily_presence', 'release_integrity': 'not_assessed',
            'consumer_acceptance': 'not_assessed', 'symbols': results}
