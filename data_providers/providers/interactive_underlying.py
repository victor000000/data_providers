"""Session-aware US spot observations and bounded raw minute references; no store writes."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
from .us_option_data import headers
from .interactive_options import futu_snapshot
from .option_quote import SourceUnavailable, number

BASE = 'https://data.alpaca.markets/v2/stocks'


def request(path, params):
    with requests.get(BASE+path, params=params, headers=headers(), timeout=(2,5)) as response:
        if response.status_code != 200:
            raise SourceUnavailable(f'Alpaca {params.get("feed")} HTTP {response.status_code}')
        return response.json()


def alpaca(symbol, feed):
    trade = request('/trades/latest', dict(symbols=symbol, feed=feed)).get('trades', {}).get(symbol) or {}
    event = trade.get('t')
    result = dict(symbol=symbol, price=number(trade.get('p')), event_time=event, source='Alpaca',
                  feed=feed, price_kind='last trade', adjustment='raw', bars=[], received_at=datetime.now(timezone.utc).isoformat())
    if not event:
        return result
    target = datetime.fromisoformat(event.replace('Z','+00:00'))-timedelta(hours=24)
    try:
        data = request('/bars', dict(symbols=symbol, timeframe='1Min', adjustment='raw',
                       feed='boats' if feed=='overnight' else feed, start=(target-timedelta(minutes=16)).isoformat(),
                       end=target.isoformat(), limit=100, sort='desc'))
        result['bars'] = [dict(start=b['t'], close=b['c'], volume=b['v']) for b in data.get('bars', {}).get(symbol, [])]
    except Exception as exc:
        result['reference_status'] = f'24h reference unavailable ({type(exc).__name__})'
    return result


def futu(symbol):
    rows = futu_snapshot(['US.'+symbol])
    if not rows:
        raise SourceUnavailable('No Futu underlying snapshot')
    r = rows[0]
    now = datetime.now(ZoneInfo('America/New_York'))
    minute = now.hour*60+now.minute
    field = 'overnight_price' if minute < 240 or minute >= 1200 else 'pre_price' if minute < 570 else 'last_price' if minute < 960 else 'after_price'
    return dict(symbol=symbol, price=number(r.get(field)), source='Futu', feed='snapshot', price_kind=field,
                event_time=None, snapshot_update_time=r.get('update_time'), bars=[], adjustment='unknown',
                received_at=datetime.now(timezone.utc).isoformat(),
                reference_status='Field event timestamp unverified; 24h return unavailable')


def theta(symbol):
    import csv
    import io
    from .thetadata import ThetaDataClient
    client = ThetaDataClient()
    params = dict(symbol=symbol, format='csv')
    if client.api_key:
        params['api_key'] = client.api_key
    with requests.get(client.base_url+'/stock/snapshot/quote', params=params, timeout=(2,5)) as response:
        if response.status_code != 200:
            raise SourceUnavailable(f'Theta stock quote HTTP {response.status_code}')
        rows = list(csv.DictReader(io.StringIO(response.text)))
    if not rows:
        raise SourceUnavailable('No Theta stock quote')
    r = rows[0];bid, ask = number(r.get('bid')), number(r.get('ask'))
    event = datetime.fromisoformat(r['timestamp'])
    if event.tzinfo is None:
        event = event.replace(tzinfo=ZoneInfo('America/New_York'))
    return dict(symbol=symbol, price=(bid+ask)/2 if bid is not None and ask is not None and 0<bid<=ask else None,
                event_time=event.astimezone(timezone.utc).isoformat(), source='ThetaData', feed='snapshot',
                price_kind='quote midpoint', bars=[], adjustment='raw', received_at=datetime.now(timezone.utc).isoformat())
