"""Rebuild derived ATM marks from the nearest valid stored smile strike."""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys
import duckdb
from data_providers.data import db


def build_repairs(con):
    con.execute('''CREATE OR REPLACE TEMP TABLE atm_marks AS
        SELECT v.underlying,v.expiry,v.day,v.spot,v.atm_iv old_iv,g.strike,
               avg(g.iv) expected
        FROM iv_history v JOIN option_greeks_daily g
          ON v.underlying=g.underlying AND v.expiry=g.expiry AND v.day=g.day
        WHERE v.source='alpaca_atm' AND g.source='alpaca_atm'
          AND v.spot>0 AND isfinite(v.spot) AND g.iv>0 AND isfinite(g.iv)
        GROUP BY 1,2,3,4,5,6
        QUALIFY row_number() OVER(PARTITION BY v.underlying,v.expiry,v.day
                   ORDER BY abs(g.strike-v.spot),g.strike)=1''')
    con.execute('''CREATE OR REPLACE TEMP TABLE atm_repairs AS SELECT * FROM atm_marks
        WHERE abs(old_iv-expected)>1e-10''')
    return con.execute('SELECT count(*),max(abs(old_iv-expected)) FROM atm_repairs').fetchone()


def apply_repairs(con, archive):
    archive=pathlib.Path(archive);archive.parent.mkdir(parents=True,exist_ok=True)
    if archive.exists():
        raise FileExistsError(archive)
    path=str(archive).replace("'","''")
    con.execute(f"ATTACH '{path}' AS saved")
    con.execute('''CREATE TABLE saved.iv_history AS SELECT v.* FROM iv_history v
        JOIN atm_repairs r USING(underlying,expiry,day) WHERE v.source='alpaca_atm' ''')
    con.execute('CREATE TABLE saved.repair_marks AS SELECT * FROM atm_repairs')
    archived=con.execute('SELECT count(*) FROM saved.iv_history').fetchone()[0]
    expected=con.execute('SELECT count(*) FROM atm_repairs').fetchone()[0]
    con.execute('DETACH saved')
    if archived!=expected:
        raise RuntimeError('Archive count does not match repair count')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    before=con.execute('SELECT count(*) FROM iv_history').fetchone()[0]
    con.execute('BEGIN')
    try:
        con.execute('''UPDATE iv_history AS v SET atm_iv=r.expected FROM atm_repairs r
            WHERE v.source='alpaca_atm' AND v.underlying=r.underlying
              AND v.expiry=r.expiry AND v.day=r.day''')
        pending=con.execute('''SELECT count(*) FROM iv_history v JOIN atm_repairs r
            USING(underlying,expiry,day) WHERE v.source='alpaca_atm'
            AND abs(v.atm_iv-r.expected)>1e-10''').fetchone()[0]
        if pending or con.execute('SELECT count(*) FROM iv_history').fetchone()[0]!=before:
            raise RuntimeError('ATM repair verification failed')
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK');raise
    return {'updated':expected,'archive':str(archive),'archive_sha256':digest,'rows_preserved':before}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--database',type=pathlib.Path,default=db.HISTORY)
    args=parser.parse_args()
    con=duckdb.connect(str(args.database),read_only=not args.apply);db.tune(con)
    count,difference=build_repairs(con)
    report={'checked_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'candidates':count,
            'maximum_iv_change':difference,'applied':args.apply,
            'examples':con.execute('SELECT * FROM atm_repairs ORDER BY abs(old_iv-expected) DESC LIMIT 15').fetchall()}
    if args.apply and count:
        stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        report.update(apply_repairs(con,pathlib.Path('var/quarantine')/f'atm_iv_before_rebuild_{stamp}.duckdb'))
    con.close()
    path=pathlib.Path('var/status/atm_summary_rebuild_20261017'+('_applied' if args.apply else '_preview')+'.json')
    path.write_text(json.dumps(report,default=str,indent=2));print(json.dumps(report,default=str),flush=True)


if __name__=='__main__':
    main()
