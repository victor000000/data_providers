"""Additional underlying reference feeds, kept separate from executable option books."""
from datetime import datetime, timezone
import requests
from .option_quote import number, SourceUnavailable
from .yahoo_session import create_session


def tiingo(symbol):
    from ..jobs.data_ops.fetch_extended_hours_prices import load_tiingo_key
    key = load_tiingo_key()
    if not key:
        raise SourceUnavailable('Tiingo credentials unavailable')
    with requests.get('https://api.tiingo.com/iex/', params={'tickers':symbol},
                      headers={'Authorization':'Token '+key}, timeout=(2,5)) as response:
        if response.status_code != 200:
            raise SourceUnavailable(f'Tiingo IEX HTTP {response.status_code}')
        rows = response.json()
    r = next((r for r in rows if r.get('ticker','').upper()==symbol), None)
    if not r:
        raise SourceUnavailable('No Tiingo observation')
    return dict(symbol=symbol, price=number(r.get('last')), event_time=r.get('lastSaleTimestamp'),
                source='Tiingo', feed='IEX', price_kind='last trade', bars=[], adjustment='raw',
                received_at=datetime.now(timezone.utc).isoformat())


def yahoo(symbol):
    with create_session() as session:
        response = session.get('https://query1.finance.yahoo.com/v8/finance/chart/'+symbol,
                               params=dict(interval='1m', range='5d', includePrePost='true'), timeout=6)
        if response.status_code != 200:
            raise SourceUnavailable(f'Yahoo chart HTTP {response.status_code}')
        results = response.json().get('chart', {}).get('result') or []
    if not results:
        raise SourceUnavailable('No Yahoo chart result')
    r = results[0];q = r['indicators']['quote'][0];now = datetime.now(timezone.utc)
    valid = [(t, c) for t,c,v in zip(r.get('timestamp', []),q['close'],q['volume'])
             if t+60<=now.timestamp() and number(c) and number(v)]
    if not valid:
        raise SourceUnavailable('No completed Yahoo positive-volume minute bar')
    t, price = max(valid)
    return dict(symbol=symbol, price=price, event_time=datetime.fromtimestamp(t+60,timezone.utc).isoformat(),
                source='Yahoo', feed='chart reference', price_kind='completed 1-minute close (bar-end clock)',
                bars=[], adjustment='unverified', received_at=now.isoformat(),
                reference_status='Chart reference only; no verified overnight or raw adjustment basis')
