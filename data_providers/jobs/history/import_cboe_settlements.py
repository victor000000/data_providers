"""Offline import of retained official SPXW settlement values and receipt versions."""
import argparse
import fcntl
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
import duckdb
from data_providers.data.schema.option_research import DDL
from data_providers.lake import literal
from data_providers.runtime import data_home

TABLE = 'index_option_settlements'
SOURCE_TYPES = {'W': 'cboe_official_weekly_settlement', 'M': 'cboe_official_eom_settlement',
                'S': 'cboe_official_standard_pm_settlement'}


def run(delivery, *, home=None):
    home = Path(home or data_home())
    content = Path(delivery).read_bytes()
    metadata = json.loads(content)
    sources = {}
    for item in metadata['sources']:
        raw = Path(item['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Source checksum mismatch')
        url = urlparse(item['url'])
        if item['http_status'] != 200 or url.scheme != 'https' or url.netloc != 'www.cboe.com' or not any(url.path.startswith('/index_settlement_values/get_sv_data/'+kind+'/') for kind in SOURCE_TYPES):
            raise ValueError('Unexpected settlement source')
        sources[item['sha256']] = (item, json.loads(raw)['data'])
    rows, keys = [], set()
    for row in metadata['rows']:
        receipt, original = sources[row['source_sha256']]
        matches = [r for r in original if r['trading_symbol'] == row['trading_symbol'] and r['expiration_date'] == row['expiration_date']]
        if len(matches) != 1 or any(matches[0].get(k) != row.get(k) for k in matches[0]):
            raise ValueError('Settlement differs from retained source')
        if row['trading_symbol'] != 'SPXW' or row['settlement_symbol'] != 'SPX' or row['source'] != SOURCE_TYPES[urlparse(receipt['url']).path.split('/')[3]]:
            raise ValueError('Unsupported settlement identity')
        if row['published_at'] is not None or row['available_at'] is not None or row['received_at'] != receipt['received_at'] or row['source_url'] != receipt['url']:
            raise ValueError('Invalid clock or provenance')
        value = Decimal(str(row['settlement_value']))
        if not value.is_finite() or value <= 0:
            raise ValueError('Invalid settlement value')
        key = (row['trading_symbol'], row['expiration_date'], row['source_sha256'])
        if key in keys:
            raise ValueError('Duplicate settlement key')
        keys.add(key)
        rows.append((row['source'], row['trading_symbol'], row['settlement_symbol'], row['expiration_date'], value, None, None, row['received_at'], receipt['url'], row['source_sha256'], json.dumps(matches[0], sort_keys=True)))
    if not rows:
        raise ValueError('No settlement rows')
    target = home/'market_history.duckdb'
    if not target.is_file():
        raise FileNotFoundError(target)
    stage = home/'staging'/('cboe-settlements-'+hashlib.sha256(content).hexdigest()[:16]+'.duckdb')
    lock_path = home/'var/maintenance/writer.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stage.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(stage)) as c:
            c.execute(DDL[TABLE])
            c.execute('CREATE TEMP TABLE incoming AS SELECT * FROM '+TABLE+' LIMIT 0')
            c.executemany('INSERT INTO incoming VALUES ('+','.join(['?']*11)+')', rows)
            if c.execute(f'SELECT count(*) FROM (SELECT * FROM {TABLE} EXCEPT SELECT * FROM incoming)').fetchone()[0]:
                raise ValueError('Conflicting retained stage')
            c.execute(f'INSERT INTO {TABLE} SELECT * FROM incoming EXCEPT SELECT * FROM {TABLE}')
        with duckdb.connect(str(target)) as c:
            c.execute('ATTACH '+literal(stage)+' AS stage (READ_ONLY)')
            c.execute('BEGIN')
            try:
                c.execute(DDL[TABLE])
                before = c.execute(f'SELECT count(*) FROM main.{TABLE}').fetchone()[0]
                # Same-receipt conflicts fail the primary key; never overwrite.
                c.execute(f'INSERT INTO main.{TABLE} SELECT * FROM stage.{TABLE} EXCEPT SELECT * FROM main.{TABLE}')
                if c.execute(f'SELECT count(*) FROM (SELECT * FROM stage.{TABLE} EXCEPT SELECT * FROM main.{TABLE})').fetchone()[0]:
                    raise ValueError('Canonical inclusion mismatch')
                added = c.execute(f'SELECT count(*) FROM main.{TABLE}').fetchone()[0]-before
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK')
                raise
    result = dict(rows=len(rows), new_canonical_rows=added, stage=str(stage), exact_merge=True, network_requests=0, publication='pending')
    stage.with_suffix('.receipt.json').write_text(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery', required=True)
    print(json.dumps(run(parser.parse_args().delivery)))


if __name__ == '__main__':
    main()
