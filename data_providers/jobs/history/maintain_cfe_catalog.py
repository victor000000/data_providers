"""Discover CFE's public archive roster and backfill non-VX futures.

VX/UX use maintain_vix. Other option products retain discovered references and
explicit gaps until their normalization is verified; never call them UX options.
"""
import json
import re
from datetime import UTC, date, datetime, timedelta

from bs4 import BeautifulSoup

from data_providers.jobs.history.futures_store import check, merge, open_stage, upsert
from data_providers.providers.cboe_futures import CFEArchive, values


def products(html):
    for script in BeautifulSoup(html,'html.parser').find_all('script'):
        text=script.text
        if not text.startswith('self.__next_f.push('):continue
        try:
            item=json.loads(text[text.find('(')+1:text.rfind(')')])
            payload=item[1] if len(item)>1 and isinstance(item[1],str) else ''
            match=re.search(r'"products":\s*(\[.*?\])',payload)
            if match:
                result=json.loads(match[1])
                if result and all('label' in x and 'value' in x for x in result):return result
        except (ValueError,TypeError):continue
    raise ValueError('CFE archive product roster was not returned')


def run(home,args):
    run_id=args.resume or 'catalog_cfe_'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    folder=home/'var/maintenance/catalogs'/run_id;folder.mkdir(parents=True,exist_ok=True)
    client=CFEArchive(folder/'raw')
    # These are official archive rosters, including retired products, not an assumed root list.
    import requests
    rosters={}
    for kind in ['futures','oof']:
        response=requests.get('https://www.cboe.com/markets/us/futures/market-statistics/historical-data/'+kind,timeout=(10,45))
        (folder/(kind+'-roster.html')).write_bytes(response.content);response.raise_for_status()
        rosters[kind]=products(response.text)
    (folder/'rosters.json').write_text(json.dumps(rosters))
    path,con=open_stage(home,run_id);stamp=datetime.now(UTC)
    start,anchor=date.today()-timedelta(days=730),date.today()-timedelta(days=1)
    checkpoint=folder/'progress.json';done=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    for product in rosters['futures']:
        root=product['value']
        if root=='VX':continue
        if done.get(root)==str(anchor):continue
        try:
            listing=client.listing('product/list/'+root+'/')
            inventory=[x for xs in listing.values() for x in xs if date.fromisoformat(x['expire_date'])>=start]
            (folder/(root+'-inventory.json')).write_text(json.dumps(inventory))
            total=0
            for item in inventory:
                expiration=date.fromisoformat(item['expire_date'])
                # Display contains source TAS designation; identity uses its primary root.
                left,right=item['product_display'].split('/')
                symbol=left.split('+')[0]+right
                cid='CFE:'+symbol+':'+str(expiration)
                upsert(con,'us_futures_contracts',[('cboe',cid,root,'CFE','FUT',symbol,expiration,None,None,
                    '100' if root=='VXM' else None,'USD',None,stamp,json.dumps(item))])
                bars=[]
                for row in client.rows(item):
                    day=date.fromisoformat(row['Trade Date'])
                    if not start<=day<=anchor:continue
                    if day>expiration:raise ValueError('CFE daily row follows expiry')
                    ohlc,settle,volume,oi=values(row)
                    bars.append(('cboe',cid,root,'CFE',day,*ohlc,settle,volume,oi,None))
                upsert(con,'us_futures_daily',bars);total+=len(bars)
            check(con,run_id,root,'daily','complete' if total else 'empty',total,venue='CFE')
            done[root]=str(anchor);checkpoint.write_text(json.dumps(done))
            print(json.dumps({'root':root,'contracts':len(inventory),'rows':total}),flush=True)
        except Exception as exc:  # noqa: BLE001 - retain other products when an archive fails
            check(con,run_id,root,'daily','failed',detail=type(exc).__name__+': '+str(exc)[:150],venue='CFE')
    for product in rosters['oof']:
        if product['value']=='UX':continue
        root=product['value']
        try:
            inventory=client.listing('options_product/list/'+root+'/')
            (folder/(root+'-option-inventory.json')).write_text(json.dumps(inventory))
            check(con,run_id,root,'option_reference','complete' if inventory else 'empty',len(inventory),venue='CFE')
            check(con,run_id,root,'option_daily','unsupported',detail='Non-UX option identity normalization requires verified product specifications; raw underlying inventory retained',venue='CFE')
        except Exception as exc:  # noqa: BLE001 - preserve failed source receipts
            check(con,run_id,root,'option_reference','failed',detail=type(exc).__name__,venue='CFE')
    con.close();proof=merge(home,path)
    (folder/'result.json').write_text(json.dumps(dict(proof,futures_products=len(rosters['futures']),option_products=len(rosters['oof']))))

    return 2  # Non-UX options remain explicitly unsupported by this adapter.
