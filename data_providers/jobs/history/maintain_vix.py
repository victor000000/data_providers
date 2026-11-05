"""Backfill exchange-listed VX futures and UX options, including expired UX archives."""
import argparse
import concurrent.futures
import fcntl
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta

from data_providers.jobs.history.futures_store import check, merge, open_stage, upsert
from data_providers.providers.cboe_futures import CFEArchive, future_identity, option_identity, values
from data_providers.runtime import data_home


def run(args):
    home = data_home()
    run_id = args.resume or 'vix_'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
    folder = home/'var/maintenance/vix'/run_id
    folder.mkdir(parents=True, exist_ok=True)
    client = CFEArchive(folder/'raw')
    path, con = open_stage(home, run_id)
    start, anchor = date.today()-timedelta(days=730), date.today()-timedelta(days=1)
    stamp = datetime.now(UTC)
    checkpoint = folder/'checkpoint.json'
    done = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    futures = [x for xs in client.listing('product/list/VX/').values() for x in xs if date.fromisoformat(x['expire_date']) >= start]
    by_month = {date.fromisoformat(x['expire_date']).strftime('%b %Y'): future_identity(x)[0]
                for x in futures if x['duration_type'] == 'M'}
    for item in futures:
        cid, symbol, expiration = future_identity(item)
        upsert(con, 'us_futures_contracts', [('cboe', cid, 'VIX', 'CFE', 'FUT', symbol, expiration,
            None, None, '1000', 'USD', None, stamp, json.dumps(item))])
    tasks = [('future', x, None) for x in sorted(futures, key=lambda x: x['expire_date'], reverse=True)]
    for underlying in client.listing('options_product/list/UX/'):
        month = underlying['label'].removeprefix('VX Monthly ')
        if month not in by_month:
            raise ValueError('UX underlying month lacks a qualified VX definition: '+month)
        options = client.listing('options_product/list/UX/'+underlying['value']+'/')
        tasks.extend(('option', x, by_month[month]) for x in sorted(options, key=lambda x:x['expire_date'], reverse=True)
                     if date.fromisoformat(x['expire_date']) >= start)
    (folder/'inventory.json').write_text(json.dumps(tasks))
    pending = [t for t in tasks if done.get(t[1]['path']) != str(anchor)]
    print(json.dumps({'run_id':run_id, 'futures':len(futures), 'options':sum(t[0]=='option' for t in tasks), 'pending':len(pending)}), flush=True)
    def fetch(task):
        try:
            return task, client.rows(task[1]), None
        except Exception as exc:  # noqa: BLE001 - retain per-contract source failures
            return task, [], type(exc).__name__+': '+str(exc)[:150]
    def publish_checkpoint():
        nonlocal con
        con.close()
        with (home/'var/maintenance/writer.lock').open('a') as lock:
            if not os.environ.get('DATA_PROVIDERS_MAINTENANCE_LOCKED'):
                fcntl.flock(lock, fcntl.LOCK_EX)
            proof = merge(home, path, locked=True)
        (folder/'available.json').write_text(json.dumps(proof, indent=2))
        _, con = open_stage(home, run_id)
    failed = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for index, (task, rows, error) in enumerate(pool.map(fetch, pending), 1):
                kind, item, underlying = task
                if error:
                    failed += 1
                    check(con, run_id, 'VIX', kind, 'failed', detail=item['path']+': '+error, venue='CFE')
                    continue
                try:
                    bars, settlements = [], []
                    if kind == 'future':
                        cid, symbol, expiration = future_identity(item)
                    else:
                        cid, expiration, strike, cp = option_identity(item)
                        metadata = dict(item, underlying_contract_id=underlying)
                        upsert(con, 'us_futures_contracts', [('cboe', cid, 'VIX', 'CFE', 'FOP', item['product_display'], expiration,
                            strike, cp, '1000', 'USD', None, stamp, json.dumps(metadata))])
                    for row in rows:
                        day = date.fromisoformat(row['Trade Date'])
                        if not start <= day <= anchor:
                            continue
                        if day > expiration:
                            raise ValueError('CFE row follows contract expiry')
                        prices, settle, volume, oi = values(row)
                        if kind == 'future':
                            bars.append(('cboe', cid, 'VIX', 'CFE', day, *prices, settle, volume, oi, None))
                        else:
                            if volume:
                                bars.append(('cboe', cid, 'VIX', 'CFE', None, expiration, strike, cp, day, *prices, volume))
                            settlements.append(('cboe', cid, 'VIX', 'CFE', underlying, expiration, strike, cp, day, settle, volume, oi))
                    upsert(con, 'us_futures_daily' if kind=='future' else 'us_futures_option_daily', bars)
                    upsert(con, 'us_futures_option_settlements', settlements)
                except Exception as exc:  # noqa: BLE001 - quarantine the contract, continue other archives
                    failed += 1
                    check(con, run_id, 'VIX', kind, 'failed', detail=item['path']+': '+type(exc).__name__+': '+str(exc)[:150], venue='CFE')
                    continue
                check(con, run_id, 'VIX', kind, 'complete' if bars or settlements else 'empty', len(bars)+len(settlements), detail=item['path'], venue='CFE')
                done[item['path']] = str(anchor)
                temp = checkpoint.with_suffix('.tmp'); temp.write_text(json.dumps(done)); temp.replace(checkpoint)
                if index == len(futures) or index % 1000 == 0:
                    publish_checkpoint()
                if index % 100 == 0 or index == len(pending):
                    print(json.dumps({'processed':index,'pending':len(pending),'failed':failed,'last':item['product_display']}), flush=True)
        con.close()
        with (home/'var/maintenance/writer.lock').open('a') as lock:
            if not os.environ.get('DATA_PROVIDERS_MAINTENANCE_LOCKED'):
                fcntl.flock(lock, fcntl.LOCK_EX)
            proof = merge(home, path, locked=True)
        summary = dict(proof, run_id=run_id, failed=failed, futures=len(futures), option_contracts=sum(t[0]=='option' for t in tasks))
        (folder/'result.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
        return 2 if failed else 0
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error('workers must be between 1 and 8')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
