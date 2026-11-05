"""Backfill all source-listed CN option strikes missing current historical coverage."""
import argparse
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from data_providers.data.schema import cn
from data_providers.jobs.data_ops.cn_frame_store import BAR_COLUMNS
from data_providers.jobs.history.futures_store import number
from data_providers.jobs.history.maintain_cn_futures import merge_stage
from data_providers.lake import connect_store
from data_providers.providers.sina_futures import SinaFutures
from data_providers.runtime import data_home


def option_rows(data,venue,product,contract,known_expiry,anchor):
    match=re.fullmatch(r'[A-Za-z]+\d{3,4}-?([CPcp])-?([\d.]+)',contract)
    if not match:
        raise ValueError('Invalid option identity')
    rows=[]
    start=anchor-timedelta(days=730)
    for raw in data:
        values=[raw[k] for k in ('o','h','l','c','v','d')] if isinstance(raw,dict) else raw
        if len(values)!=6:
            raise ValueError('Malformed option bar')
        o,h,l,c,v=map(number,values[:5]);day=date.fromisoformat(str(values[5])[:10])
        if not start<=day<=anchor:
            continue
        if c is None or (h is not None and l is not None and h<l):
            raise ValueError('Invalid option prices')
        if known_expiry is not None and day>known_expiry:
            raise ValueError('Option bar follows expiry')
        rows.append(('sina_all_strikes',contract,product,known_expiry,float(match[2]),match[1].upper(),
                     day,o,h,l,c,v,None,venue,product,None,None,None,None))
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quotes-stage',default='latest')
    parser.add_argument('--resume')
    args=parser.parse_args();home=data_home();anchor=date.today()-timedelta(days=1)
    run_id=args.resume or datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
    folder=home/'var/maintenance/cn_option_history'/run_id;folder.mkdir(parents=True,exist_ok=True)
    path=home/'staging/futures'/('cn_options_'+run_id+'.duckdb');path.parent.mkdir(parents=True,exist_ok=True)
    if args.quotes_stage=='latest':
        candidates=list((home/'var/maintenance/cn_futures').glob('*/result.json'))
        if not candidates:
            raise FileNotFoundError('No completed CN chain snapshot is available')
        report=max(candidates,key=lambda p:p.stat().st_mtime_ns)
        args.quotes_stage=json.loads(report.read_text())['stage']
    with duckdb.connect(str(Path(args.quotes_stage)),read_only=True) as quotes:
        inventory=quotes.execute('SELECT DISTINCT venue,product,contract FROM option_quotes ORDER BY venue,product,contract').fetchall()
    with connect_store(home,'cn') as published:
        coverage={(v,c.lower()):d for v,c,d in published.execute('SELECT venue,contract,max(day) FROM option_bars GROUP BY 1,2').fetchall()}
        expiry_rows=published.execute("SELECT venue,regexp_extract(lower(contract),'^([a-z]+[0-9]{3,4})',1),min(expiry),max(expiry) FROM option_bars GROUP BY 1,2").fetchall()
        expiries={(v,p):lo for v,p,lo,hi in expiry_rows if lo==hi}
    con=duckdb.connect(str(path));con.execute("SET TimeZone='UTC'");cn.init(con)
    client=SinaFutures(folder/'raw');skipped=0;failed=0;empty=0;total=0;consecutive=0
    try:
        for index,(venue,product,contract) in enumerate(inventory,1):
            prefix=re.match(r'^[A-Za-z]+\d{3,4}',contract)[0].lower()
            known_expiry=expiries.get((venue,prefix))
            target=min(anchor,known_expiry) if known_expiry else anchor
            if coverage.get((venue,contract.lower()),date.min)>=target:
                skipped+=1;continue
            receipt=con.execute("SELECT status FROM option_history_checks WHERE source='sina_all_strikes' AND venue=? AND contract=? AND target_day=?",[venue,contract,target]).fetchone()
            if receipt and receipt[0] in ('complete','empty'):
                skipped+=1;continue
            status,detail,count='failed','',0
            try:
                rows=option_rows(client.option_daily(contract),venue,product,contract,known_expiry,anchor)
                if rows:
                    frame=pd.DataFrame.from_records(rows,columns=BAR_COLUMNS)
                    frame['expiry']=pd.to_datetime(frame['expiry'])
                    con.register('_bars',frame)
                    try:
                        con.execute('INSERT OR REPLACE INTO option_bars SELECT * FROM _bars')
                    finally:
                        con.unregister('_bars')
                count=len(rows);total+=count
                status='complete' if rows else 'empty'
                empty+=int(not rows);consecutive=0
                if known_expiry is None:
                    detail='Source has no verified expiry; retained NULL rather than estimating'
            except Exception as exc:  # noqa: BLE001 - failed contracts remain explicit and retryable
                failed+=1
                consecutive=consecutive+1 if not isinstance(exc,(ValueError,KeyError)) else 0
                detail=type(exc).__name__+': '+str(exc)[:200]
            con.execute('INSERT OR REPLACE INTO option_history_checks VALUES (?,?,?,?,?,?,?,?,?)',
                ['sina_all_strikes',venue,product,contract,target,status,count,datetime.now(UTC),detail])
            progress={'inventory':len(inventory),'processed':index,'rows':total,'skipped':skipped,'empty':empty,'failed':failed,'contract':contract}
            (folder/'progress.json').write_text(json.dumps(progress,indent=2))
            if index%25==0:
                print(json.dumps(progress),flush=True)
            if consecutive>=12:
                print('Source circuit breaker: 12 failures; unfinished inventory retained',flush=True)
                break
    finally:
        con.close()
    merge_stage(home,path,tables=('option_bars','option_history_checks'))
    result={'stage':str(path),'verified':True,'inventory':len(inventory),'processed':index if inventory else 0,
            'rows':total,'skipped':skipped,'empty':empty,'failed':failed,'unattempted':len(inventory)-(index if inventory else 0)}
    (folder/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
    return int(bool(failed or result['unattempted']))


if __name__=='__main__':
    raise SystemExit(main())
