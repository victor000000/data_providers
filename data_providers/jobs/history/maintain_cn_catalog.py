"""Provider-wide CN futures discovery, deliverable histories and confirmed option chains."""
import json
import re
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import duckdb

from data_providers.data.schema import cn
from data_providers.jobs.history.maintain_cn_futures import daily_rows, merge_stage, quotes
from data_providers.providers.sina_futures import BASE, SinaFutures


def root_and_venue(symbol, venue):
    match=re.fullmatch(r'([A-Za-z]+)(\d{3,4})',symbol)
    if not match or int(match[2])==0:
        raise ValueError('Not a deliverable CN contract')
    root=match[1].upper()
    return root, 'INE' if venue=='SHFE' and root in {'SC','NR','LU','BC','EC'} else venue


def run(home,args):
    from akshare.utils import demjson
    run_id=args.resume or 'catalog_cn_'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    folder=home/'var/maintenance/catalogs'/run_id;folder.mkdir(parents=True,exist_ok=True)
    client=SinaFutures(folder/'raw')
    text=client.get('https://vip.stock.finance.sina.com.cn/quotes_service/view/js/qihuohangqing.js').content.decode('gb18030')
    catalog=demjson.decode(text[text.find('{'):text.find('}')+1])
    (folder/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False))
    path=home/'staging/futures'/(run_id+'.duckdb')
    con=duckdb.connect(str(path));cn.init(con)
    progress=folder/'progress.json';results=json.loads(progress.read_text()) if progress.exists() else {}
    start,anchor=date.today()-timedelta(days=730),date.today()-timedelta(days=1)
    root_scope=set()
    for venue,products in catalog.items():
        for name,node,*_ in products[1:]:
            key=venue+':'+node
            if results.get(key,{}).get('anchor')==str(anchor):
                continue
            record={'name':name,'node':node,'venue':venue.upper(),'failures':[],'futures_rows':0,'option_quotes':0,'anchor':None}
            results[key]=record
            try:
                contracts=client.get('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQFuturesData',
                    {'page':1,'num':1000,'sort':'position','asc':0,'node':node,'base':'futures'}).json()
                if not isinstance(contracts,list):raise TypeError('Invalid CN contract inventory')
                actual=[]
                for item in contracts:
                    symbol=item['symbol']
                    try:root,exchange=root_and_venue(symbol,venue.upper())
                    except ValueError:continue
                    actual.append((root,exchange,symbol))
                    root_scope.add((exchange,root))
                record['listed_contracts']=[x[2] for x in actual]
                if not actual:raise ValueError('Source returned no deliverable contracts')
                roots={(r,v) for r,v,s in actual}
                if len(roots)!=1:raise ValueError('Product node contains multiple identities')
                root,exchange=next(iter(roots))
                # Historical month candidates do not become listed contracts by guessing.
                symbols={s for r,v,s in actual}
                for offset in range(24):
                    month=anchor.year*12+anchor.month-1-offset
                    yy,mm=divmod(month,12)
                    symbols.add(root+(str(yy%10) if exchange=='CZCE' else f'{yy%100:02d}')+f'{mm+1:02d}')
                for symbol in sorted(symbols):
                    try:
                        rows=[r for r in daily_rows(client.daily(symbol),exchange,root,symbol) if start<=r[3]<=anchor]
                        if rows:
                            con.executemany('INSERT OR REPLACE INTO futures_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',rows)
                            record['futures_rows']+=len(rows)
                    except Exception as exc:  # noqa: BLE001 - preserve per-contract source failures
                        record['failures'].append({'contract':symbol,'dataset':'daily','error':type(exc).__name__})
                page=BASE+f'/futures/view/optionsDP.php/{root.lower() if exchange=="CZCE" else root.lower()+"_o"}/{exchange.lower()}'
                try:
                    months=[m for m in client.months(page) if re.fullmatch(root+r'\d{3,4}',m,re.IGNORECASE)]
                    record['confirmed_option_months']=months
                    for month in months:
                        product_param=root.lower() if exchange=='CZCE' else root.lower()+'_o'
                        rows=quotes(client.chain(product_param,exchange.lower(),month),exchange,root,month,datetime.now(UTC))
                        if rows:
                            con.executemany('INSERT OR REPLACE INTO option_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
                            record['option_quotes']+=len(rows)
                except Exception as exc:  # noqa: BLE001 - no chain is not proof of no listed options
                    record['failures'].append({'dataset':'option_chain','error':type(exc).__name__})
                if not record['failures']:record['anchor']=str(anchor)
            except Exception as exc:  # noqa: BLE001 - continue other source product nodes
                record['failures'].append({'dataset':'inventory','error':type(exc).__name__})
            temp=progress.with_suffix('.tmp');temp.write_text(json.dumps(results,ensure_ascii=False,indent=2));temp.replace(progress)
            print(json.dumps(record,ensure_ascii=False),flush=True)
    con.close()
    merge_stage(home,path)
    report={'run_id':run_id,'catalog_nodes':sum(len(p)-1 for p in catalog.values()),'source_roots':sorted(root_scope),
            'products':results,'stage':str(path),'verified_merge':True,'scope':'Sina returned inventories; source omissions remain gaps'}
    (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    # Include every confirmed strike from this expanded inventory in daily history maintenance.
    command=[sys.executable,'-m','data_providers.jobs.history.maintain_cn_option_history','--quotes-stage',str(path)]
    code=subprocess.run(command,check=False).returncode
    if code:raise RuntimeError('Expanded CN option history incomplete; retained staging and receipts')

    return 2 if any(r['failures'] for r in results.values()) else 0
