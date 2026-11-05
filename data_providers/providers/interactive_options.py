"""Read-only interactive Futu/Theta option quotes; never substitute last for a book."""
import csv
import io
import os
import socket
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests
from .option_quote import SourceUnavailable, contract, quote
from .thetadata import ThetaDataClient


def futu_code(symbol):
    s = contract(symbol)
    return f"US.{s['underlying']}{s['expiry'].replace('-', '')[2:]}{s['right']}{round(s['strike']*1000)}"


def futu_snapshot(codes):
    from futu import OpenQuoteContext, RET_OK
    host = os.getenv('FUTU_HOST', '127.0.0.1')
    port = int(os.getenv('FUTU_PORT', '11111'))
    with socket.create_connection((host, port), timeout=3):
        pass
    ctx = OpenQuoteContext(host=host, port=port)
    try:
        status, data = ctx.get_market_snapshot(codes)
        if status != RET_OK:
            raise SourceUnavailable('Futu US options permission missing. Enable the required OpenAPI US options entitlement in your Futu account.' if 'permission' in str(data).lower() else 'Futu snapshot unavailable; check OpenD login and quote permission')
        return data.to_dict('records')
    finally:
        ctx.close()


def futu(symbols):
    mapping = {futu_code(s): s for s in symbols}
    rows = []
    for r in futu_snapshot(list(mapping)):
        symbol = mapping.get(r.get('code'))
        if not symbol:
            continue
        # Snapshot update_time is not a verified bid/ask event timestamp.
        q = quote(symbol, r.get('bid_price'), r.get('ask_price'), r.get('bid_vol'), r.get('ask_vol'),
                  source='Futu', feed='snapshot', snapshot_update_time=r.get('update_time'),
                  timestamp_basis='US/Eastern snapshot clock; book event time/delay unknown')
        rows.append(dict(**contract(symbol), quote=q))
    return dict(rows=rows, source='Futu', warning='Futu snapshot book timestamp and delay are unverified.')


def theta(symbols):
    client = ThetaDataClient()
    specs = {s: contract(s) for s in symbols}
    rows = []
    for root, expiry in sorted({(s['underlying'], s['expiry']) for s in specs.values()}):
        params = dict(symbol=root, expiration=expiry, format='csv')
        if client.api_key:
            params['api_key'] = client.api_key
        with requests.get(client.base_url+'/option/snapshot/quote', params=params, timeout=(2,5)) as response:
            if response.status_code != 200:
                raise SourceUnavailable(f'Theta snapshot HTTP {response.status_code}')
            if len(response.content) > 8_000_000:
                raise SourceUnavailable('Theta snapshot exceeded interactive size limit')
            for r in csv.DictReader(io.StringIO(response.text)):
                symbol = f"{root}{expiry.replace('-','')[2:]}{r['right'][0].upper()}{round(float(r['strike'])*1000):08d}"
                if symbol not in specs:
                    continue
                stamp = datetime.fromisoformat(r['timestamp'])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=ZoneInfo('America/New_York'))
                q = quote(symbol, r.get('bid'), r.get('ask'), r.get('bid_size'), r.get('ask_size'),
                          source='ThetaData', feed='live', quote_time=stamp.astimezone(timezone.utc).isoformat())
                rows.append(dict(**specs[symbol], quote=q))
    return dict(rows=rows, source='ThetaData')
