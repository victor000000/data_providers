"""Capture all source-listed CN strikes/months and contract-specific futures bars.

Snapshot observation time is not a historical trading date. Existing exchange
EOD/IV maintenance remains responsible for option history and settlements.
"""
import argparse
import json
import os
import re
import time
import uuid
from datetime import UTC, date, datetime
from urllib.parse import urlparse

import duckdb

from data_providers.data.schema import cn
from data_providers.data.schema.futures import CN_OPTION_CHECK_DDL
from data_providers.data.schema.futures import CN_QUOTE_DDL as QUOTE_DDL
from data_providers.jobs.history.futures_store import number
from data_providers.lake import connect_store, ident, literal
from data_providers.providers.sina_futures import BASE, SinaFutures, source_name
from data_providers.runtime import data_home


def quotes(data,venue,product,month,stamp):
    rows=[]
    for side,key in [('C','up'),('P','down')]:
        for row in data.get(key,[]):
            if len(row)<8:
                raise ValueError('Malformed source quote row')
            contract=str(row[-1]).strip()
            match=re.fullmatch(r'[A-Za-z]+\d{3,4}-?([CPcp])-?([\d.]+)',contract)
            if not match or match[1].upper()!=side or re.match(r'^[A-Za-z]+',contract)[0].upper()!=product.upper():
                raise ValueError('Invalid option contract identity')
            strike=number(match[2])
            if strike is None:
                raise ValueError('Invalid option strike')
            source_month=re.match(r'^[A-Za-z]+(\d{3,4})',contract)[1]
            requested_month=re.match(r'^[A-Za-z]+(\d{3,4})',month)[1]
            if source_month!=requested_month[-len(source_month):]:
                raise ValueError('Source returned a different contract month')
            # No synthetic OHLC, IV or historical trade date is inferred from a quote.
            rows.append(('sina_chain',venue,product,contract,month.upper(),
                         'INDEX_OPTION' if venue=='CFFEX' else 'FUTURES_OPTION',strike,side,
                         stamp,number(row[2]),number(row[1]),number(row[3]),number(row[5]),json.dumps(row)))
    return rows


def daily_rows(data,venue,product,contract):
    rows=[]
    for row in data:
        if isinstance(row,dict):
            values=[row.get(key) for key in ('d','o','h','l','c','v','p','s')]
            if values[0] is None:
                values=list(row.values())
        else:
            values=row
        if len(values)!=8:
            raise ValueError('Malformed futures daily row')
        day=date.fromisoformat(str(values[0])[:10])
        o,h,l,c,v,oi,settle=map(number,values[1:])
        if c is None or (h is not None and l is not None and h<l):
            raise ValueError('Invalid futures daily prices')
        rows.append((venue,product,contract.upper(),day,o,h,l,c,settle,v,oi,'sina_contract'))
    return rows


def merge_stage(home,path,tables=('futures_daily','option_quotes')):
    import fcntl
    lockpath=home/'var/maintenance/writer.lock'
    lockpath.parent.mkdir(parents=True,exist_ok=True)
    with lockpath.open('a') as lock:
        # Wait for supervised CN writers; do not steal a tiny gap between its steps.
        while not os.environ.get('DATA_PROVIDERS_MAINTENANCE_LOCKED'):
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                time.sleep(10)
        with duckdb.connect(str(home/'cn_market.duckdb')) as target:
            target.execute("SET TimeZone='UTC'")
            target.execute(QUOTE_DDL)
            target.execute(CN_OPTION_CHECK_DDL)
            target.execute(f'ATTACH {literal(path)} AS staged (READ_ONLY)')
            target.execute('BEGIN')
            try:
                for table in tables:
                    info=target.execute(f'PRAGMA table_info({literal(table)})').fetchall()
                    keys=[r[1] for r in info if r[5]]
                    fields=[r[1] for r in info if not r[5]]
                    updates=','.join(f'{ident(n)}=EXCLUDED.{ident(n)}' for n in fields)
                    target.execute(f"INSERT INTO {table} SELECT * FROM staged.{table} ON CONFLICT ({','.join(map(ident,keys))}) DO UPDATE SET {updates}")
                    if target.execute(f'SELECT count(*) FROM (SELECT * FROM staged.{table} EXCEPT SELECT * FROM {table})').fetchone()[0]:
                        raise ValueError(f'{table} merge verification failed')
                target.execute('COMMIT')
            except Exception:
                target.execute('ROLLBACK');raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--resume')
    p.add_argument('--products',nargs='*')
    p.add_argument('--history-months',type=int,default=0)
    args=p.parse_args()
    home=data_home();run_id=args.resume or datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
    folder=home/'var/maintenance/cn_futures'/run_id;folder.mkdir(parents=True,exist_ok=True)
    path=home/'staging/futures'/('cn_'+run_id+'.duckdb');path.parent.mkdir(parents=True,exist_ok=True)
    with connect_store(home,'cn') as reader:
        products=reader.execute('SELECT venue,product,ak_symbol FROM cn_product_map ORDER BY venue,product').fetchall()
    client=SinaFutures(folder/'raw');menu=client.products()
    con=duckdb.connect(str(path));con.execute("SET TimeZone='UTC'");cn.init(con);con.execute(QUOTE_DDL)
    progress=folder/'progress.json'
    results=json.loads(progress.read_text()) if args.resume and progress.exists() else []
    if args.products:
        products=[r for r in products if r[1] in set(args.products)]
    try:
        for venue,product,name in products:
            record={'venue':venue,'product':product,'quotes':0,'future_bars':0,'failures':[]}
            results=[r for r in results if r['product']!=product];results.append(record)
            try:
                if venue=='CFFEX':
                    page=BASE+'/futures/view/optionsCffexDP.php/mo/cffex'
                else:
                    provider_name=source_name(product,name)
                    page=menu.get(provider_name,BASE+f'/futures/view/optionsDP.php/{product.lower()}_o/{venue.lower()}')
                parts=urlparse(page).path.rstrip('/').split('/')
                prod_param,venue_param=parts[-2:]
                months=client.months(page)
                months=[m for m in months if re.match(r'^([A-Za-z]+)\d',m) and re.match(r'^([A-Za-z]+)\d',m)[1].upper()==product.upper()]
                record['listed_months']=list(months)
                if args.history_months:
                    current=date.today().year*12+date.today().month-1
                    old=[f'{product.lower()}{n//12%100:02d}{n%12+1:02d}' for n in range(current-args.history_months,current)]
                    record['requested_historical_months']=old
                    months=list(dict.fromkeys(months+old))
                if not months:
                    raise ValueError('Source returned no matching contract months')
                for month in months:
                    try:
                        chain_data=client.chain(prod_param,venue_param,month)
                        rows=quotes(chain_data,venue,product,month,datetime.now(UTC))
                        if rows:
                            con.executemany('INSERT OR REPLACE INTO option_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
                        record['quotes']+=len(rows)
                        if venue!='CFFEX':
                            bars=daily_rows(client.daily(month),venue,product,month)
                            if bars:
                                con.executemany('INSERT OR REPLACE INTO futures_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',bars)
                            record['future_bars']+=len(bars)
                    except Exception as exc:  # noqa: BLE001 - per-product failures are recorded
                        record['failures'].append({'month':month,'error':type(exc).__name__})
            except Exception as exc:  # noqa: BLE001 - per-product failures are recorded
                record['failures'].append({'error':type(exc).__name__})
            (folder/'progress.json').write_text(json.dumps(results,indent=2))
            print(json.dumps(record),flush=True)
    finally:
        con.close()
    print('Download complete; waiting for supervised maintenance before verified merge',flush=True)
    merge_stage(home,path)
    (folder/'result.json').write_text(json.dumps({'stage':str(path),'retained':True,'verified':True,'products':results},indent=2))
    print('CN contract and quote staging merged and verified',flush=True)


if __name__=='__main__':
    main()
