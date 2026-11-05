"""Merge only retained Alpaca fill-minute tables; keep staging and exact receipts."""
import contextlib
import fcntl
import json
import os
from datetime import datetime, UTC
from pathlib import Path

import duckdb
from data_providers.runtime import data_home


def merge(home):
    home = Path(home)
    stage = home / 'staging/alpaca.duckdb'
    if not stage.exists():
        return {'status': 'no_stage', 'tables': []}
    results = []
    with duckdb.connect(str(stage), read_only=True) as source, duckdb.connect(str(home / 'market_history.duckdb')) as target:
        for table in ['option_minutes', 'underlying_minutes']:
            rows = source.execute('SELECT * FROM '+table).fetchall()
            schema = source.execute('DESCRIBE '+table).fetchall()
            assert [(r[0],r[1]) for r in schema] == [(r[0],r[1]) for r in target.execute('DESCRIBE '+table).fetchall()]
            target.execute('CREATE TEMP TABLE incoming AS SELECT * FROM '+table+' LIMIT 0')
            if rows:
                target.executemany('INSERT INTO incoming VALUES ('+','.join('?' for _ in schema)+')',rows)
            target.execute('BEGIN')
            try:
                conflicts = target.execute('SELECT count(*) FROM (SELECT i.* FROM incoming i JOIN '+table+' t USING(source,symbol,ts_utc) EXCEPT ALL SELECT * FROM '+table+')').fetchone()[0]
                if conflicts:
                    raise ValueError(f'{table}: {conflicts} conflicting retained minute rows; stage preserved')
                before = target.execute('SELECT count(*) FROM '+table).fetchone()[0]
                target.execute('INSERT INTO '+table+' SELECT * FROM incoming ON CONFLICT DO NOTHING')
                assert target.execute('SELECT count(*) FROM (SELECT * FROM incoming EXCEPT ALL SELECT * FROM '+table+')').fetchone()[0] == 0
                added = target.execute('SELECT count(*) FROM '+table).fetchone()[0]-before
                target.execute('COMMIT')
            except Exception:
                target.execute('ROLLBACK')
                raise
            target.execute('DROP TABLE incoming')
            results.append(dict(table=table,staged_rows=len(rows),new_rows=added,exact_merge=True))
    return dict(observed_utc=datetime.now(UTC).isoformat(),tables=results,stage_retained=True,network_requests=0)


def main():
    home = data_home()
    lock_path = home/'var/maintenance/writer.lock'
    with contextlib.ExitStack() as stack:
        if not os.environ.get('DATA_PROVIDERS_MAINTENANCE_LOCKED'):
            lock = stack.enter_context(lock_path.open('a'))
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result = merge(home)
        out = home/'var/maintenance/fill-minute-merge'
        out.mkdir(exist_ok=True)
        path = out/(datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        path.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))


if __name__ == '__main__':
    main()
