"""Import hash-verified retained Theta intraday Greeks without source requests.

Nonpositive IV/spot rows retain their quality flags and original values. This
never promotes unknown quote or availability clocks to the interval timestamp.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import duckdb
from data_providers.data.schema.option_research import DDL
from data_providers.lake import literal
from data_providers.runtime import data_home

TABLE = 'option_intraday_greeks'
KEYS = ('source','contract_id','interval_ts','frequency_seconds','model_version')


def run(manifest, *, home=None):
    home = Path(home or data_home())
    manifest = Path(manifest)
    metadata = json.loads(manifest.read_text())
    paths = []
    expected = 0
    for item in metadata['reports']:
        path = Path(item['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Retained Parquet checksum mismatch')
        paths.append(str(path)); expected += item['rows']
    if not paths:
        raise ValueError('No retained Greek observations')
    identity = hashlib.sha256(manifest.read_bytes()).hexdigest()
    stage = home/'staging'/('theta-intraday-greeks-'+identity[:16]+'.duckdb')
    stage.parent.mkdir(parents=True, exist_ok=True)
    lock_path = home/'var/maintenance/writer.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with duckdb.connect(str(stage)) as c:
            c.execute(DDL[TABLE])
            c.execute('CREATE TEMP VIEW retained AS SELECT * FROM read_parquet(['+
                      ','.join(literal(p) for p in paths)+'], hive_partitioning=false)')
            if c.execute('SELECT count(*) FROM retained').fetchone()[0] != expected:
                raise ValueError('Retained row count mismatch')
            if c.execute("""SELECT count(*) FROM retained WHERE source IS DISTINCT FROM 'thetadata_v3'
                 OR frequency_seconds IS NULL OR frequency_seconds<=0
                 OR interval_ts IS NULL OR model_version IS NULL
                 OR quote_ts IS NOT NULL OR available_at IS NOT NULL
                 OR quality IS NULL OR receipt_sha256 IS NULL""").fetchone()[0]:
                raise ValueError('Invalid source, sampling or unknown-clock contract')
            c.execute("""CREATE TEMP VIEW incoming AS SELECT
                source,contract_id,underlying,CAST(session_date AS DATE) AS session_date,
                interval_ts,frequency_seconds,model_version,quote_ts,available_at,received_at,
                underlying_ts,underlying_price,
                CAST(json_extract_string(raw_json,'$.bid') AS DOUBLE) AS bid,
                CAST(json_extract_string(raw_json,'$.ask') AS DOUBLE) AS ask,
                iv,delta,CAST(json_extract_string(raw_json,'$.gamma') AS DOUBLE) AS gamma,
                CAST(json_extract_string(raw_json,'$.theta') AS DOUBLE) AS theta,
                CAST(json_extract_string(raw_json,'$.vega') AS DOUBLE) AS vega,
                CAST(json_extract_string(raw_json,'$.rho') AS DOUBLE) AS rho,
                CAST(json_extract_string(raw_json,'$.iv_error') AS DOUBLE) AS iv_error,
                NULL::VARCHAR AS model,
                '{"status":"vendor_native_units_unverified","iv":"decimal","delta":"fraction"}'::JSON AS units_json,
                quality,receipt_sha256,CAST(raw_json AS JSON) AS raw_json FROM retained""")
            if c.execute('SELECT count(*) FROM (SELECT '+','.join(KEYS)+
                         ' FROM incoming GROUP BY ALL HAVING count(*)>1)').fetchone()[0]:
                raise ValueError('Duplicate retained observation keys')
            cols = [r[1] for r in c.execute("PRAGMA table_info('"+TABLE+"')").fetchall()]
            mismatch = ' OR '.join('s."'+x+'" IS DISTINCT FROM t."'+x+'"' for x in cols)
            join = ','.join(KEYS)
            if c.execute(f'SELECT count(*) FROM incoming s JOIN {TABLE} t USING({join}) WHERE '+mismatch).fetchone()[0]:
                raise ValueError('Conflicting retained stage')
            missing = ' AND '.join('t.'+k+'=s.'+k for k in KEYS)
            c.execute(f'INSERT INTO {TABLE} SELECT s.* FROM incoming s WHERE NOT EXISTS(SELECT 1 FROM {TABLE} t WHERE '+missing+')')
            if c.execute(f'SELECT count(*) FROM (SELECT * FROM incoming EXCEPT SELECT * FROM {TABLE})').fetchone()[0]:
                raise ValueError('Stage inclusion mismatch')
        # Do not initialize unrelated stores or replace existing observations.
        target = home/'market_history.duckdb'
        if not target.is_file():
            raise FileNotFoundError('Canonical history store is missing')
        with duckdb.connect(str(target)) as c:
            c.execute('ATTACH '+literal(stage)+' AS stage (READ_ONLY)')
            c.execute('BEGIN')
            try:
                c.execute(DDL[TABLE])
                if c.execute(f'SELECT count(*) FROM stage.{TABLE} s JOIN main.{TABLE} t USING({join}) WHERE '+mismatch).fetchone()[0]:
                    raise ValueError('Conflicting canonical Greek observation')
                before = c.execute(f'SELECT count(*) FROM main.{TABLE}').fetchone()[0]
                c.execute(f'INSERT INTO main.{TABLE} SELECT s.* FROM stage.{TABLE} s WHERE NOT EXISTS(SELECT 1 FROM main.{TABLE} t WHERE '+missing+')')
                if c.execute(f'SELECT count(*) FROM (SELECT * FROM stage.{TABLE} EXCEPT SELECT * FROM main.{TABLE})').fetchone()[0]:
                    raise ValueError('Canonical inclusion mismatch')
                added = c.execute(f'SELECT count(*) FROM main.{TABLE}').fetchone()[0]-before
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK'); raise
    result = dict(rows=expected,new_canonical_rows=added,stage=str(stage),manifest_sha256=identity,
                  exact_merge=True,network_requests=0,publication='pending')
    (stage.with_suffix('.receipt.json')).write_text(json.dumps(result,indent=2))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.manifest)),flush=True)

if __name__=='__main__':main()
