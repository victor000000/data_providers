"""US futures and futures-option maintenance, with explicit source coverage receipts.

Discover broker contracts and all returned chain ladders, fetch session-dated
futures bars from Massive, and retain resumable staging. --full includes every
returned contract within the historical window, including expired futures.
"""
import argparse
import asyncio
import json
import re
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from data_providers.jobs.history.futures_store import (
    check,
    merge,
    normalize_contract_ids,
    number,
    open_stage,
    upsert,
)
from data_providers.providers.massive_futures import FuturesREST
from data_providers.runtime import data_home


def universe(path):
    products = []
    for line in Path(path).read_text().splitlines():
        if line.strip().startswith('FUT:'):
            _,root,venue = line.strip().split(':')
            if (root,venue) not in products:
                products.append((root,venue))
    if not products:
        raise ValueError('No FUT: roots in the configured universe')
    return products


def expiry(value):
    value = str(value)
    return date.fromisoformat(value[:4]+'-'+value[4:6]+'-'+value[6:8]) if len(value)>=8 else None


def broker_symbol(root):
    return {'6E':'EUR','6J':'JPY'}.get(root,root)


def contract_key(contract,venue):
    if contract.secType=='FUT':
        return venue+':'+contract.localSymbol.replace(' ','')+':'+str(expiry(contract.lastTradeDateOrContractMonth))
    return 'IBKR:'+str(contract.conId)


def contract_row(root,venue,detail,stamp):
    c = detail.contract
    return ('ibkr',contract_key(c,venue),root,venue,c.secType,c.localSymbol,
            expiry(c.lastTradeDateOrContractMonth),number(c.strike) if c.secType=='FOP' else None,
            c.right or None,c.multiplier,c.currency,getattr(detail,'underConId',0) or None,
            stamp,json.dumps(asdict(detail),default=str))


async def historical_options(ib,contract,duration,timeout=30):
    """A permission/service failure is never treated as a valid empty series."""
    failures=[]
    def on_error(req,code,message,*args):
        bad=code in {354,420,502,504,1100,1300,10089,10167,10168} or (
            code==162 and any(word in message.lower() for word in ('pacing','cancel','timed out','timeout','permission','subscription')))
        if bad:
            failures.append(f'IBKR {code}: {message}')
    ib.errorEvent+=on_error
    try:
        bars=await asyncio.wait_for(ib.reqHistoricalDataAsync(contract,endDateTime='',durationStr=duration,
            barSizeSetting='1 day',whatToShow='TRADES',useRTH=False,formatDate=1,timeout=0),timeout)
        if failures:
            raise RuntimeError(failures[-1])
        return bars
    finally:
        ib.errorEvent-=on_error


async def discover(con, products, run_id, folder, start, full=False):
    from ib_async import IB, Future, FuturesOption

    from data_providers.providers import tws
    ib = IB()
    await ib.connectAsync(tws.HOST,tws.PORT,clientId=389,timeout=20,readonly=True)
    found = []
    stamp = datetime.now(UTC)
    today = stamp.date()
    errors = []
    ib.errorEvent += lambda req,code,msg,*args: errors.append({'req':req,'code':code,'message':msg})
    try:
        for root,venue in products:
            try:
                ds = await asyncio.wait_for(ib.reqContractDetailsAsync(
                    Future(symbol=broker_symbol(root),exchange=venue,currency='USD',includeExpired=True)),40)
                ds = [d for d in ds if d.contract.secType=='FUT' and expiry(d.contract.lastTradeDateOrContractMonth)
                      and expiry(d.contract.lastTradeDateOrContractMonth)>=start]
                if not ds:
                    raise ValueError('Broker returned no qualified futures')
                upsert(con,'us_futures_contracts',[contract_row(root,venue,d,stamp) for d in ds])
                check(con,run_id,root,'contracts','complete',len(ds),venue=venue)
                found.extend((root,venue,d) for d in ds)
                print(f'{venue}:{root}: {len(ds)} qualified futures',flush=True)
            except Exception as exc:  # noqa: BLE001 - failed source requests remain explicit
                check(con,run_id,root,'contracts','failed',detail=type(exc).__name__,venue=venue)
        # All active underlying IDs, not just a single front month. Chain strike/expiry
        # arrays are metadata: their Cartesian product is never asserted to be listed contracts.
        active = [(root,venue,d) for root,venue,d in found if expiry(d.contract.lastTradeDateOrContractMonth)>=today]
        sem = asyncio.Semaphore(3)
        option_probe = []
        chain_inventory = []
        async def chains(item):
            root,venue,d = item
            async with sem:
                await asyncio.sleep(.35)
                try:
                    result = await asyncio.wait_for(ib.reqSecDefOptParamsAsync(
                        d.contract.symbol,venue,'FUT',d.contract.conId),18)
                    chain_inventory.extend((root,venue,x) for x in result)
                    rows = [('ibkr',root,x.exchange,int(x.underlyingConId),x.tradingClass,x.multiplier,
                             today,json.dumps(sorted(x.expirations)),json.dumps(sorted(x.strikes)),stamp)
                            for x in result]
                    upsert(con,'us_futures_option_chains',rows)
                    check(con,run_id,root,'option_chains','complete' if rows else 'empty',len(rows),
                          detail=str(d.contract.conId),venue=venue)
                    if result and not option_probe:
                        option_probe.append((root,venue,result[0]))
                except Exception as exc:  # noqa: BLE001 - failed source requests remain explicit
                    check(con,run_id,root,'option_chains','failed',detail=f'{d.contract.conId}: {type(exc).__name__}',venue=venue)
        await asyncio.gather(*(chains(item) for item in active))
        # Test the actual FOP historical channel before attempting thousands of requests.
        if option_probe:
            root,venue,chain = option_probe[0]
            exp = min(e for e in chain.expirations if e>=today.strftime('%Y%m%d'))
            strike = sorted(chain.strikes)[len(chain.strikes)//2]
            candidate = FuturesOption(symbol=broker_symbol(root),lastTradeDateOrContractMonth=exp,
                                      strike=strike,right='C',exchange=chain.exchange,
                                      multiplier=chain.multiplier,currency='USD',tradingClass=chain.tradingClass)
            try:
                ds = await asyncio.wait_for(ib.reqContractDetailsAsync(candidate),20)
                if not ds:
                    raise ValueError('FOP probe could not be qualified')
                upsert(con,'us_futures_contracts',[contract_row(root,venue,d,stamp) for d in ds])
                contract = ds[0].contract
                # timeout=0 disables the SDK's empty-on-timeout conversion.
                bars = await historical_options(ib,contract,'5 D',20)
                rows = [('ibkr',contract_key(contract,venue),root,venue,int(chain.underlyingConId),
                         expiry(contract.lastTradeDateOrContractMonth),contract.strike,contract.right,
                         b.date,number(b.open),number(b.high),number(b.low),number(b.close),number(b.volume)) for b in bars if date.fromisoformat(str(b.date)[:10])<stamp.date()]
                upsert(con,'us_futures_option_daily',rows)
                check(con,run_id,root,'option_daily','probe_only' if rows else 'empty',len(rows),
                      detail='A probe is not full-chain historical coverage',venue=venue)
                if full:
                    await backfill_fop(ib,con,run_id,chain_inventory,stamp)

            except Exception as exc:  # noqa: BLE001 - failed source requests remain explicit
                check(con,run_id,root,'option_daily','blocked',detail=type(exc).__name__,venue=venue)
        else:
            check(con,run_id,'ALL','option_daily','blocked',detail='No option chain available for historical probe')
    finally:
        (folder/'gateway-errors.json').write_text(json.dumps(errors,indent=2))
        ib.disconnect()
    return found


async def backfill_fop(ib,con,run_id,chains,stamp):
    """All confirmed live FOP contracts, only after a responsive historical probe.

    IBKR cannot supply expired-option history. Do not turn strike/expiry Cartesian
    products into identities: ask for the actual contract definitions per expiry/class.
    """
    from ib_async import FuturesOption

    from data_providers.shared.net import throttle
    seen=set()
    for root,venue,chain in chains:
        for exp in sorted(chain.expirations):
            if exp<stamp.strftime('%Y%m%d'):
                continue
            key=(root,venue,int(chain.underlyingConId),chain.tradingClass,exp)
            if key in seen:
                continue
            seen.add(key)
            try:
                candidate=FuturesOption(symbol=broker_symbol(root),lastTradeDateOrContractMonth=exp,
                    exchange=chain.exchange,multiplier=chain.multiplier,currency='USD',tradingClass=chain.tradingClass)
                ds=await asyncio.wait_for(ib.reqContractDetailsAsync(candidate),45)
                ds=[d for d in ds if int(d.underConId)==int(chain.underlyingConId)]
                upsert(con,'us_futures_contracts',[contract_row(root,venue,d,stamp) for d in ds])
                for detail in ds:
                    contract=detail.contract
                    if contract.secType!='FOP':
                        continue
                    await asyncio.to_thread(throttle,'ibkr')
                    bars=await historical_options(ib,contract,'2 Y',30)
                    rows=[('ibkr',contract_key(contract,venue),root,venue,int(chain.underlyingConId),
                        expiry(contract.lastTradeDateOrContractMonth),contract.strike,contract.right,
                        b.date,number(b.open),number(b.high),number(b.low),number(b.close),number(b.volume)) for b in bars if date.fromisoformat(str(b.date)[:10])<stamp.date()]
                    upsert(con,'us_futures_option_daily',rows)
                    check(con,run_id,root,'option_daily','complete' if rows else 'empty',len(rows),
                          detail=str(contract.conId),venue=venue)
            except TimeoutError:
                check(con,run_id,root,'option_daily','blocked',detail='Historical service timed out; remaining contracts retained for retry',venue=venue)
                return
            except Exception as exc:  # noqa: BLE001 - preserve per-chain failure receipts
                check(con,run_id,root,'option_daily','failed',detail=type(exc).__name__,venue=venue)
    check(con,run_id,'ALL','expired_option_daily','unsupported',detail='IBKR does not provide expired FOP history')


def bar_row(root,venue,ticker,bar,contract_id=None):
    # A session starts on the previous evening. The provider's session_end_date
    # is authoritative, never derive the trading day from its UTC start timestamp.
    day = date.fromisoformat(bar['session_end_date'])
    values = [number(bar.get(key)) for key in ('open','high','low','close')]
    if any(v is None for v in values) or values[1]<values[2]:
        raise ValueError('Invalid futures OHLC')
    start = datetime.fromtimestamp(int(bar['window_start'])/1_000_000_000,UTC)
    return ('massive',contract_id or venue+':'+ticker,root,venue,day,*values,number(bar.get('settlement_price')),
            number(bar.get('volume')),None,start)


def run(args):
    home = data_home()
    run_id = args.resume or datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
    folder = home/'var/maintenance/us_futures'/run_id
    folder.mkdir(parents=True,exist_ok=True)
    path,con = open_stage(home,run_id)
    normalize_contract_ids(con)
    products = universe(args.universe or home/'plans/ibkr_iv_universe_scoped.txt')
    start = date.today()-timedelta(days=730)
    anchor = date.today()-timedelta(days=1)
    checkpoint_path = folder/'checkpoint.json'
    done = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
    # Carry the already downloaded front contracts into the repaired checkpoint.
    for cid,venue,symbol in con.execute("SELECT contract_id,venue,local_symbol FROM us_futures_contracts WHERE sec_type='FUT'").fetchall():
        old=venue+':'+symbol.replace(' ','')
        if old in done:
            done[cid]=done[old]
    try:
        found = asyncio.run(discover(con,products,run_id,folder,start,args.full))
        (folder/'inventory.json').write_text(json.dumps([{'root':r,'venue':v,'contract':asdict(d.contract)} for r,v,d in found],default=str))
        queue,others = [],[]
        for root,venue in products:
            items = sorted([d for r,v,d in found if r==root and v==venue],key=lambda d:expiry(d.contract.lastTradeDateOrContractMonth))
            active = [d for d in items if expiry(d.contract.lastTradeDateOrContractMonth)>=anchor]
            if active:
                queue.append((root,venue,active[0]))
            others.extend((root,venue,d) for d in items if not active or d.contract.conId!=active[0].contract.conId)
        front_count = len(queue)
        if args.full:
            queue.extend(others)
        client = FuturesREST(folder/'raw/massive')
        for index,(root,venue,detail) in enumerate(queue,1):
            c = detail.contract
            ticker = c.localSymbol.replace(' ','')
            identity = contract_key(c,venue)
            # A one-digit vendor year cannot safely identify two decade-separated
            # contracts in the same historical window. Keep distant definitions,
            # but do not attribute nearer prices to an unverified distant contract.
            if expiry(c.lastTradeDateOrContractMonth)>anchor+timedelta(days=365*7):
                check(con,run_id,root,'daily','unsupported',detail=identity+': provider year ambiguity',venue=venue)
                continue
            if index==front_count+1:
                con.close()
                proof=merge(home,path)
                (folder/'front-ready.json').write_text(json.dumps(proof,indent=2))
                _,con=open_stage(home,run_id)
            if not re.fullmatch(r'[A-Z0-9]+[FGHJKMNQUVXZ]\d{1,2}',ticker):
                check(con,run_id,root,'daily','failed',detail='Unsupported provider symbol: '+ticker,venue=venue)
                continue
            if done.get(identity)==str(anchor):
                continue
            try:
                bars = list(client.pages('/futures/v1/aggs/'+ticker,
                    {'resolution':'1session','window_start.gte':str(start-timedelta(days=1)),
                     'window_start.lte':str(anchor),'limit':50000,'sort':'window_start.asc'}))
                if any(date.fromisoformat(b['session_end_date'])>expiry(c.lastTradeDateOrContractMonth) for b in bars):
                    raise ValueError('Provider bars follow the qualified contract expiry')
                rows = [bar_row(root,venue,ticker,b,identity) for b in bars if start<=date.fromisoformat(b['session_end_date'])<=anchor]
                upsert(con,'us_futures_daily',rows)
                check(con,run_id,root,'daily','complete' if rows else 'empty',len(rows),detail=identity,venue=venue)
                done[identity] = str(anchor)
                temp = folder/'checkpoint.tmp';temp.write_text(json.dumps(done,indent=2));temp.replace(checkpoint_path)
                print(f'{index}/{len(queue)} {identity}: {len(rows)} session bars',flush=True)
            except Exception as exc:  # noqa: BLE001 - failed source requests remain explicit
                check(con,run_id,root,'daily','failed',detail=identity+': '+type(exc).__name__+' '+str(exc)[:100],venue=venue)
                print(f'{identity}: failed {type(exc).__name__}',flush=True)
        con.close()
        proof = merge(home,path)
        (folder/'complete.json').write_text(json.dumps(proof,indent=2))
        # Explicit partial status: successful futures bars never imply FOP history completion.
        summary = {'run_id':run_id,'stage':str(path),'products':len(products),'contracts':len(found),
                   'historical_contract_requests':len(queue),'coverage':'Check futures_ingestion_checks; FOP probe is not full coverage'}
        with duckdb.connect(str(path),read_only=True) as reader:
            summary['coverage_status']=reader.execute('SELECT dataset,status,count(*),sum(rows) FROM futures_ingestion_checks GROUP BY 1,2 ORDER BY 1,2').fetchall()
        summary['options_history_complete']=False
        (folder/'result.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary),flush=True)
        return summary
    finally:
        con.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--universe')
    p.add_argument('--full',action='store_true',help='Backfill every discovered historical/current futures contract')
    p.add_argument('--resume',help='Resume an existing run without repeating completed contract downloads')
    args = p.parse_args()
    result=run(args)
    return 2 if any(row[1] in {'failed','blocked','unsupported'} for row in result['coverage_status']) else 0


if __name__=='__main__':
    raise SystemExit(main())
