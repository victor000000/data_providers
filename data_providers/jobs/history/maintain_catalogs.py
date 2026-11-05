"""Discover provider-wide futures catalogs and backfill actual listed contract series.

Provider catalog coverage is not universal exchange coverage: ICE/unsupported
FOP history are explicit gaps. Raw reference pages, price responses and stages
remain available for resumption.
"""
import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from data_providers.jobs.history.futures_store import check, merge, open_stage, upsert
from data_providers.jobs.history.maintain_us_futures import bar_row
from data_providers.providers.massive_futures import FuturesREST
from data_providers.runtime import data_home

VENUES = {'XCME':'CME','XCBT':'CBOT','XCEC':'COMEX','XNYM':'NYMEX'}


def price_queue(contracts, done, start, anchor, roots=None):
    """Preserve the inventory while selecting unfinished eligible price requests."""
    selected, deferred = [], {}
    for cid, item in contracts.items():
        if done.get(cid) == str(anchor):
            continue
        if date.fromisoformat(item['last_trade_date']) < start:
            deferred[cid] = 'expired_before_window'
        elif item.get('first_trade_date') and date.fromisoformat(item['first_trade_date']) > anchor:
            deferred[cid] = 'first_trade_after_cutoff'
        elif roots and item.get('type') != 'single':
            deferred[cid] = 'combo_deferred'
        elif roots and item['product_code'] not in roots:
            deferred[cid] = 'other_root_deferred'
        else:
            selected.append((cid,item))
    selected.sort(key=lambda pair:(
        pair[1]['last_trade_date'] > str(anchor+timedelta(days=365)) if roots else False,
        pair[1]['last_trade_date'],pair[0]))
    return selected, deferred


def run_prices(home, args, folder, path, con, start, anchor, contracts):
    run_id = args.resume or folder.name
    progress = folder/'prices.json'
    done = json.loads(progress.read_text()) if progress.exists() else {}
    roots = set(args.priority_roots.split(',')) if args.priority_roots else None
    ordered, deferred = price_queue(contracts,done,start,anchor,roots)
    policy = 'priority' if roots else 'all'
    manifest = {'policy':policy,'roots':sorted(roots) if roots else [],'anchor':str(anchor),
                'pending':len(ordered),'near_term_pending':sum(item['last_trade_date']<=str(anchor+timedelta(days=365)) for _,item in ordered),
                'deferred':deferred,'selected_contracts':[cid for cid,_ in ordered],
                'prior_completed_requests':len(done),'raw_cache_reuse':True}
    (folder/(policy+'-queue.json')).write_text(json.dumps(manifest,indent=2)+'\n')
    client = FuturesREST(folder/'raw',reuse_success=True)
    for i,(cid,item) in enumerate(ordered,1):
        try:
            before = client.responses_reused
            bars=list(client.pages('/futures/v1/aggs/'+item['ticker'], {'resolution':'1session',
                'window_start.gte':str(start-timedelta(days=1)),'window_start.lte':str(anchor),'limit':50000,'sort':'window_start.asc'}))
            if any(date.fromisoformat(b['session_end_date'])>date.fromisoformat(item['last_trade_date']) for b in bars):
                raise ValueError('Provider prices follow the qualified expiry')
            venue=VENUES.get(item['trading_venue'],item['trading_venue'])
            rows=[bar_row(item['product_code'],venue,item['ticker'],b,cid) for b in bars if start<=date.fromisoformat(b['session_end_date'])<=anchor]
            metadata_upsert(con,'us_futures_daily',rows)
            check(con,run_id,item['product_code'],'daily','complete' if rows else 'empty',len(rows),detail=cid,venue=venue)
            done[cid]=str(anchor)
            temp=progress.with_suffix('.tmp');temp.write_text(json.dumps(done));temp.replace(progress)
            print(json.dumps({'phase':'prices','policy':policy,'processed':i,'contracts':len(ordered),
                              'completed_requests':len(done),'contract':cid,'rows':len(rows),
                              'cached_pages':client.responses_reused-before}),flush=True)
        except Exception as exc:  # noqa: BLE001 - retain raw and staged data for retry
            check(con,run_id,item['product_code'],'daily','failed',detail=cid+': '+type(exc).__name__)
            print(json.dumps({'phase':'price_failure','contract':cid,'error':type(exc).__name__}),flush=True)
    con.close()
    proof=merge(home,path)
    result=dict(proof,status='priority_attempts_complete_broad_work_deferred' if roots else 'completed_with_source_gaps',
                policy=policy,deferred=len(deferred),selected=len(ordered),
                successful_selected=sum(done.get(cid)==str(anchor) for cid,_ in ordered),
                responses_reused=client.responses_reused)
    (folder/('priority-result.json' if roots else 'result.json')).write_text(json.dumps(result))
    return 2


def metadata_upsert(con, table, rows):
    """A discovery snapshot is one transaction, not one disk commit per contract."""
    con.execute('BEGIN')
    try:
        upsert(con, table, rows)
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise


def reference_row(item, stamp):
    venue = VENUES.get(item['trading_venue'], item['trading_venue'])
    expiration = date.fromisoformat(item['last_trade_date'])
    cid = venue+':'+item['ticker']+':'+str(expiration)
    return ('massive',cid,item['product_code'],venue,'BAG' if item.get('type')=='combo' else 'FUT',item['ticker'],expiration,None,None,
            None,item.get('trade_currency_code'),None,stamp,json.dumps(item))


def run_window(folder, *, today=None):
    """Pin reference and price dates across restarts, including legacy runs."""
    state = folder / 'window.json'
    if state.exists():
        window = json.loads(state.read_text())
        start, anchor = date.fromisoformat(window['start']), date.fromisoformat(window['anchor'])
        if start > anchor:
            raise ValueError('Invalid futures run window')
        return start, anchor
    today = today or datetime.now(UTC).date()
    anchor = today - timedelta(days=1)
    # Existing runs predate window.json. Their successful price receipts are
    # authoritative; never move the target forward and invalidate those receipts.
    receipts = folder / 'prices.json'
    if receipts.exists():
        targets = set(json.loads(receipts.read_text()).values())
        if len(targets) > 1:
            raise ValueError('Mixed target dates in futures price receipts')
        if targets:
            anchor = date.fromisoformat(targets.pop())
    elif (folder / 'contracts.json').exists():
        dates = {row['date'] for row in json.loads((folder / 'contracts.json').read_text()).values() if row.get('date')}
        if len(dates) > 1:
            raise ValueError('Mixed reference dates in futures catalog')
        if dates:
            anchor = date.fromisoformat(dates.pop())
    start = anchor - timedelta(days=729)
    temporary = state.with_suffix('.tmp')
    temporary.write_text(json.dumps({'start': str(start), 'anchor': str(anchor)}))
    temporary.replace(state)
    return start, anchor


def run_us(home, args):
    from data_providers.jobs.audit.publish_futures_catalog import product_rows
    from data_providers.lake import digest
    run_id = args.resume or 'catalog_us_'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    folder = home/'var/maintenance/catalogs'/run_id; folder.mkdir(parents=True,exist_ok=True)
    path, con = open_stage(home, run_id)
    start, anchor = run_window(folder)
    if args.prices_only:
        saved = folder/'contracts.json'
        state = folder/'discovery.json'
        if not args.resume or not saved.exists() or not state.exists() or not json.loads(state.read_text()).get('complete'):
            raise ValueError('Price-only resume requires completed retained discovery')
        contracts = json.loads(saved.read_text())
        count = con.execute("SELECT count(*) FROM us_futures_contracts WHERE source='massive'").fetchone()[0]
        completed = con.execute("SELECT count(*) FROM futures_ingestion_checks WHERE run_id=? "
                                "AND dataset='contract_catalog' AND status='complete' AND rows=?",
                                [run_id,len(contracts)]).fetchone()[0]
        if count != len(contracts) or not completed:
            raise ValueError('Retained stage metadata is incomplete; full discovery resume required')
        return run_prices(home,args,folder,path,con,start,anchor,contracts)
    client = FuturesREST(folder/'raw')
    stamp = datetime.now(UTC)
    # Reuse the discovery already requested in this session. All raw pages stay intact.
    seed = home/'var/maintenance'/('discovery-'+(anchor+timedelta(days=1)).strftime('%Y%m%d'))/'raw'
    cached = sorted(seed.glob('*.json'), key=lambda p:p.stat().st_mtime) if seed.exists() else []
    products, contracts, next_url = [], {}, None
    product_receipts = []
    for raw in cached:
        payload = json.loads(raw.read_text()); rows=[x for x in payload.get('results',[]) if x.get('date')==str(anchor)]
        if rows and 'ticker' in rows[0]:
            contracts.update({reference_row(x,stamp)[1]:x for x in rows})
            next_url = payload.get('next_url')
        elif rows and 'product_code' in rows[0]:
            products.extend(rows)
            product_receipts.append({'path':str(raw),'sha256':digest(raw),'rows':len(rows)})
    if not products:
        products=list(client.pages('/futures/v1/products',{'date':str(anchor),'limit':1000}))
    metadata_upsert(con,'us_futures_products',product_rows(products, {
        'response_receipts': product_receipts, 'raw_folder': str(folder/'raw')}))
    saved = folder/'contracts.json'
    state = folder/'discovery.json'
    if saved.exists():
        contracts.update(json.loads(saved.read_text()))
    if state.exists():
        next_url = json.loads(state.read_text()).get('next_url')
    if not state.exists() or not json.loads(state.read_text()).get('complete'):
        # Reference pagination is resumed from its last successfully stored page.
        route = next_url.removeprefix('https://api.massive.com') if next_url else '/futures/v1/contracts'
        params = {} if next_url else {'date':str(anchor),'limit':1000,'active':'true'}
        # Store every page with its continuation before doing slow price backfills.
        seen_routes=set()
        while route:
            if route in seen_routes:raise ValueError('Repeated futures reference continuation')
            seen_routes.add(route)
            rows=list(client.pages_one(route,params))
            for x in rows:
                contracts[reference_row(x,stamp)[1]]=x
            temporary=saved.with_suffix('.tmp');temporary.write_text(json.dumps(contracts));temporary.replace(saved)
            continuation=client.next_url
            temporary=state.with_suffix('.tmp');temporary.write_text(json.dumps({'next_url':continuation,'complete':not continuation,'contracts':len(contracts)}));temporary.replace(state)
            print(json.dumps({'phase':'contract_discovery','products':len(products),'contracts':len(contracts)}),flush=True)
            route=continuation.removeprefix('https://api.massive.com') if continuation else None
            params={}
    refs=[reference_row(x,stamp) for x in contracts.values()]
    metadata_upsert(con,'us_futures_contracts',refs)
    check(con,run_id,'ALL','product_catalog','complete',len(products))
    check(con,run_id,'ALL','contract_catalog','complete',len(refs))
    for product,detail in [('ALL','Massive futures REST does not provide futures-option history; Gateway HMDS requests currently time out'),('ICE','ICE US is outside the configured Massive CME/CBOT/COMEX/NYMEX feed')]:
        check(con,run_id,product,'coverage_gap','blocked',detail=detail)
    return run_prices(home,args,folder,path,con,start,anchor,contracts)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--market',choices=['us','cn','cfe'],required=True)
    p.add_argument('--resume')
    p.add_argument('--prices-only',action='store_true',help='Reuse completed retained metadata without replaying discovery')
    p.add_argument('--priority-roots',help='Comma-separated roots; defer combos and other roots without deleting them')
    args=p.parse_args();home=Path(data_home())
    if args.market=='us':return run_us(home,args)
    elif args.market=='cfe':
        from data_providers.jobs.history.maintain_cfe_catalog import run
        return run(home,args)
    else:
        from data_providers.jobs.history.maintain_cn_catalog import run
        return run(home,args)


if __name__=='__main__':raise SystemExit(main())
