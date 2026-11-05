"""Receipted 510050 daily raw/qfq history and a source-qualified PV graph milestone."""
import fcntl
import json
from datetime import UTC, date, datetime

import duckdb

from data_providers.data.schema.cn_equities import ensure
from data_providers.jobs.history.calculate_daily_pv import calculate, merge_stage
from data_providers.lake import ident, literal
from data_providers.providers.cn_etf import fetch, parse
from data_providers.runtime import data_home


def merge_raw(home, path):
    """Caller holds the maintenance writer lock across both raw and derived merges."""
    with duckdb.connect(str(home / 'cn_market.duckdb')) as con:
        ensure(con)
        con.execute("SET TimeZone='UTC'")
        con.execute(f'ATTACH {literal(path)} AS staged (READ_ONLY)')
        info = con.execute("PRAGMA table_info('equity_daily')").fetchall()
        keys = [r[1] for r in info if r[5]]
        updates = ','.join(f'{ident(r[1])}=EXCLUDED.{ident(r[1])}' for r in info if not r[5])
        con.execute('BEGIN')
        try:
            con.execute('INSERT INTO equity_daily SELECT * FROM staged.equity_daily '
                        f"ON CONFLICT ({','.join(map(ident,keys))}) DO UPDATE SET {updates}")
            if con.execute('SELECT count(*) FROM (SELECT * FROM staged.equity_daily EXCEPT '
                           'SELECT * FROM equity_daily)').fetchone()[0]:
                raise ValueError('CN ETF exact merge verification failed')
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise


def build(home=None):
    home = home or data_home()
    stamp = datetime.now(UTC)
    # Explicit source-supported cutoff already confirmed by the bounded raw probe.
    start, end = date(2013, 10, 1), date(2026, 10, 16)
    folder = home / 'var/maintenance/cn-etf-510050-20261018'
    folder.mkdir(parents=True,exist_ok=True)
    run = stamp.strftime('run-%Y%m%dT%H%M%S%fZ')
    output = folder / run
    output.mkdir()
    rows, receipts = [], {}
    for adjustment in ('raw','qfq'):
        body, receipt, path = fetch(folder/'raw',start=start,end=end,adjustment=adjustment)
        parsed = parse(body,receipt,path,start=start,end=end,adjustment=adjustment)
        rows.extend(parsed)
        receipts[adjustment] = {'receipt':str(path),**receipt,'rows':len(parsed)}
    dates = {a:{r[5] for r in rows if r[6]==a} for a in ('raw','qfq')}
    if dates['raw'] != dates['qfq']:
        raise ValueError('Raw/adjusted source date inventories disagree; no silent inner-join loss')
    stage = home / 'staging/cn_equities' / (run + '.duckdb')
    stage.parent.mkdir(parents=True,exist_ok=True)
    with duckdb.connect(str(stage)) as con:
        con.execute("SET TimeZone='UTC'")
        con.execute('BEGIN')
        ensure(con)
        con.executemany('INSERT INTO equity_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
        con.execute('''CREATE TABLE inputs AS
            SELECT 'CN' AS market,r.venue,r.instrument_id,r.symbol,NULL::VARCHAR AS contract_id,r.day,r.source,
                   a.close,r.volume_native AS volume,r.volume_unit,
                   'eastmoney_qfq_snapshot;historical_adjustment_availability_unknown' AS price_convention
            FROM equity_daily r JOIN equity_daily a ON r.instrument_id=a.instrument_id AND r.day=a.day
                AND r.source=a.source AND r.venue=a.venue
            WHERE r.adjustment='raw' AND a.adjustment='qfq' ''')
        # Fresh HTTP receipts are the immutable inputs, not an earlier lake release.
        calculate(con,None,stamp)
        con.execute('UPDATE daily_indicators SET lineage=json_merge_patch(lineage,?::JSON)',
                    [json.dumps({'source_receipts':receipts,'input_kind':'hashed_HTTP_entity_bodies',
                                 'volume_scale':'unverified; never interpreted as shares',
                                 'currency':'unverified; native prices retained'})])
        counts = con.execute('SELECT field_id,count(*),count(value),min(day),max(day) '
                             'FROM daily_indicators GROUP BY field_id ORDER BY field_id').fetchall()
        con.execute('COMMIT')
    with (home/'var/maintenance/writer.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        merge_raw(home,stage)
        merge_stage(home,stage,locked=True)
    report = {'status':'raw_and_calculated_verified_merged','instrument_id':'CN:SSE:ETF:510050',
              'stage':str(stage),'raw_adjusted_rows':len(rows),'receipts':receipts,
              'field_counts_rows_valid_first_last':counts,'historical_available_at_known':False,
              'brain_equivalence_verified':False,'adjustment':'raw and qfq stored separately',
              'source_volume_unit':'vendor_native_unverified; do not apply share-volume thresholds',
              'input_release':None,'input_lineage':'Full HTTP entity bodies retained with SHA256 receipts',
              'limitations':['One eligible CN ETF underlying; not all CN equities/ETFs or an option archive',
                             'Currency and volume scale still require source verification',
                             'Historical availability, adjustment revisions and calendar continuity unverified']}
    (output/'report.json').write_text(json.dumps(report,default=str,indent=2)+'\n')
    return output,report


if __name__=='__main__':
    folder,report=build()
    print(json.dumps({'path':str(folder),**report},default=str))
