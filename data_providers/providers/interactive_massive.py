"""Bounded option snapshots with conservative pacing for the configured basic plan."""
from datetime import datetime, timezone
import threading
import time
import requests
from .massive import load_api_key, BASE
from .option_quote import SourceUnavailable, contract, quote

LOCK = threading.Lock()
NEXT = 0.0
CACHE = {}


def snapshots(symbols):
    global NEXT
    groups = {}
    for symbol in symbols:
        spec = contract(symbol)
        groups.setdefault((spec['underlying'], spec['expiry'], spec['right']), []).append(spec)
    rows = []
    def cache_key(item):
        (root, expiry, side), specs = item
        return (root, expiry, side, min(s['strike'] for s in specs), max(s['strike'] for s in specs))
    for (root, expiry, side), specs in sorted(groups.items(), key=lambda item: CACHE.get(cache_key(item), (0,))[0]):
        key = (root, expiry, side, min(s['strike'] for s in specs), max(s['strike'] for s in specs))
        with LOCK:
            cached = CACHE.get(key)
            if cached and time.monotonic()-cached[0] < 60:
                data = cached[1]
            else:
                if time.monotonic() < NEXT:
                    continue  # Keep other cached groups; rotate to this group on a later poll.
                NEXT = time.monotonic()+60
                auth = load_api_key()
                if not auth:
                    raise SourceUnavailable('Massive credentials unavailable')
                params = dict(expiration_date=expiry, contract_type='call' if side=='C' else 'put', limit=250)
                params.update({'strike_price.gte':key[3], 'strike_price.lte':key[4]})
                with requests.get(BASE+'/v3/snapshot/options/'+root, params=params,
                                  headers={'Authorization':'Bearer '+auth}, timeout=(2,5)) as response:
                    if response.status_code != 200:
                        raise SourceUnavailable(f'Massive option snapshot HTTP {response.status_code}')
                    data = response.json()
                CACHE[key] = (time.monotonic(), data)
                if len(CACHE)>50:
                    CACHE.pop(next(iter(CACHE)))
        wanted = {s['symbol']:s for s in specs}
        for row in data.get('results', []):
            details = row.get('details', {})
            symbol = details.get('ticker', '').removeprefix('O:')
            if symbol not in wanted or details.get('shares_per_contract') != 100:
                continue
            q = row.get('last_quote') or {}
            event = datetime.fromtimestamp(q['last_updated']/1e9, timezone.utc).isoformat() if q.get('last_updated') else None
            rows.append(dict(**wanted[symbol], quote=quote(symbol, q.get('bid'), q.get('ask'), q.get('bid_size'),
                             q.get('ask_size'), source='Massive', feed='live' if q.get('timeframe')=='REAL-TIME' else 'delayed_or_unknown', quote_time=event)))
    return dict(rows=rows, source='Massive', warning='Bounded 250-row snapshot; one group refreshed per minute, partial coverage while paced. Missing rows do not prove no listing.')
