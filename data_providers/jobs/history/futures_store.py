"""Verified, transactional derivatives staging; retain every staged row after merging."""
import math
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from data_providers.data.schema.futures import DDL, ensure
from data_providers.lake import ident, literal


def number(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def open_stage(home, run_id):
    path = Path(home) / 'staging/futures' / (run_id + '.duckdb')
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone='UTC'")
    ensure(con)
    return path, con


def upsert(con, table, rows):
    if not rows:
        return
    if table not in DDL:
        raise ValueError(table)
    info = con.execute(f'PRAGMA table_info({literal(table)})').fetchall()
    names = [row[1] for row in info]
    keys = [row[1] for row in info if row[5]]
    updates = ','.join(f'{ident(name)}=EXCLUDED.{ident(name)}' for name in names if name not in keys)
    conflict = f" ON CONFLICT ({','.join(map(ident,keys))}) DO UPDATE SET {updates}" if keys else ''
    con.executemany(f"INSERT INTO {ident(table)} VALUES ({','.join('?' for _ in names)}){conflict}",rows)


def check(con, run_id, product, dataset, status, rows=0, detail='', venue='', market='US'):
    con.execute('INSERT INTO futures_ingestion_checks VALUES (?,?,?,?,?,?,?,?,?)',
                [run_id,market,venue,product,dataset,status,rows,detail,datetime.now(UTC)])


def normalize_contract_ids(con):
    """Upgrade the initial ingestion run to expiry-qualified, cross-provider IDs."""
    con.execute("UPDATE us_futures_contracts SET contract_id=venue||':'||replace(local_symbol,' ','')||':'||CAST(expiry AS VARCHAR) WHERE sec_type='FUT' AND regexp_matches(contract_id,'^[0-9]+$')")
    con.execute("UPDATE us_futures_contracts SET contract_id='IBKR:'||contract_id WHERE sec_type='FOP' AND regexp_matches(contract_id,'^[0-9]+$')")
    con.execute("""UPDATE us_futures_daily b SET contract_id=COALESCE((
        SELECT min(c.contract_id) FROM us_futures_contracts c WHERE c.sec_type='FUT'
        AND c.root=b.root AND c.venue=b.venue AND c.expiry>=b.day
        AND c.expiry < b.day + INTERVAL 8 YEAR
        AND c.venue||':'||replace(c.local_symbol,' ','')=b.contract_id),b.contract_id)
        WHERE NOT regexp_matches(b.contract_id,':[0-9]{4}-[0-9]{2}-[0-9]{2}$')""")
    con.execute("UPDATE us_futures_option_daily SET contract_id='IBKR:'||contract_id WHERE regexp_matches(contract_id,'^[0-9]+$')")


def merge(home, stage, *, locked=False):
    import os
    # Standalone backfills coordinate with exchange merges and publication too.
    if not locked and not os.environ.get('DATA_PROVIDERS_MAINTENANCE_LOCKED'):
        import fcntl
        lockpath = Path(home)/'var/maintenance/writer.lock'
        lockpath.parent.mkdir(parents=True, exist_ok=True)
        with lockpath.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return _merge(home, stage)
    return _merge(home, stage)


def _merge(home, stage):
    with duckdb.connect(str(Path(home)/'market_history.duckdb')) as con:
        con.execute("SET TimeZone='UTC'")
        ensure(con)
        normalize_contract_ids(con)
        con.execute(f'ATTACH {literal(stage)} AS staged (READ_ONLY)')
        con.execute('BEGIN')
        try:
            for table in DDL:
                # Retained stages from earlier schema versions may lack newer datasets.
                if not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=? AND table_catalog='staged'", [table]).fetchone()[0]:
                    continue
                names = [r[0] for r in con.execute(f'DESCRIBE {ident(table)}').fetchall()]
                keys = [r[1] for r in con.execute(f'PRAGMA table_info({literal(table)})').fetchall() if r[5]]
                # Attempt receipts append once per exact row; data tables upsert on domain keys.
                if not keys:
                    con.execute(f'INSERT INTO {ident(table)} SELECT * FROM staged.{ident(table)} EXCEPT SELECT * FROM {ident(table)}')
                else:
                    updates = ','.join(f'{ident(n)}=EXCLUDED.{ident(n)}' for n in names if n not in keys)
                    con.execute(f"INSERT INTO {ident(table)} SELECT * FROM staged.{ident(table)} "
                                f"ON CONFLICT ({','.join(map(ident,keys))}) DO UPDATE SET {updates}")
                missing = con.execute(f'SELECT count(*) FROM (SELECT * FROM staged.{ident(table)} EXCEPT SELECT * FROM {ident(table)})').fetchone()[0]
                if missing:
                    raise ValueError(f'{table}: {missing} staged rows failed verification')
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
    return {'stage':str(stage),'retained':True,'verified':True}
