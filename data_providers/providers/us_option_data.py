"""On-demand Alpaca option metadata and quotes; no ingestion or duplicated stores."""
import json
from datetime import date, timedelta
from urllib.parse import urlencode
from .alpaca_http import get_json
from .option_quote import contract, quote
from ..credentials import alpaca_headers
from ..runtime import data_home


def headers():
    # This interactive workspace uses the user's paper credential profile, not
    # the separate acquisition jobs' credentials.
    c = json.loads((data_home() / 'secrets/alpaca_paper.json').read_text())
    if c.get('environment') != 'paper':
        raise ValueError('Expected paper data credential profile')
    return {'APCA-API-KEY-ID': c['key_id'], 'APCA-API-SECRET-KEY': c['secret']}


def request(base, params):
    return get_json(base + '?' + urlencode(params), headers(), tries=1)


def read_snapshots(params, underlying=None, feed='auto'):
    url = 'https://data.alpaca.markets/v1beta1/options/snapshots' + ('/'+underlying if underlying else '')
    chosen, warning = ('opra' if feed == 'auto' else feed), None
    try:
        data = request(url, dict(params, feed=chosen))
    except Exception as exc:
        if feed != 'auto' or getattr(getattr(exc, 'response', None), 'status_code', getattr(exc,'code',None)) != 403:
            raise
        chosen = 'indicative'
        warning = 'OPRA access denied. Complete the OPRA agreement and verify options-data permissions in Alpaca. Indicative prices are modified, not OPRA NBBO.'
        data = request(url, dict(params, feed=chosen))
    rows = []
    for symbol, snap in data.get('snapshots', {}).items():
        q = snap.get('latestQuote') or {}
        try:
            spec = contract(symbol)
        except ValueError:
            continue
        rows.append(dict(**spec, quote=quote(symbol, q.get('bp'), q.get('ap'), q.get('bs'), q.get('as'),
                        source='Alpaca', feed=chosen, quote_time=q.get('t')),
                         greeks=snap.get('greeks'), iv=snap.get('impliedVolatility')))
    return dict(rows=sorted(rows, key=lambda r:(r['expiry'],r['strike'],r['right'])), source='Alpaca', feed=chosen,
                warning=warning, next_page_token=data.get('next_page_token'))


def chain(underlying, expiry, right, low=None, high=None, page_token=None, feed='auto'):
    params = dict(expiration_date=expiry, type='call' if right == 'C' else 'put', limit=20)
    for key, value in [('strike_price_gte',low),('strike_price_lte',high),('page_token',page_token)]:
        if value is not None and value != '':
            params[key] = value
    return read_snapshots(params, underlying, feed)


def snapshots(symbols, feed='auto'):
    if not 1 <= len(symbols) <= 50:
        raise ValueError('Request one to fifty contracts')
    for symbol in symbols:
        contract(symbol)
    return read_snapshots(dict(symbols=','.join(symbols), limit=100), feed=feed)


def expiries(underlying):
    # Broker metadata does not imply that every listed contract has quote coverage.
    params = dict(underlying_symbols=underlying, status='active', limit=1000,
                  expiration_date_gte=str(date.today()), expiration_date_lte=str(date.today()+timedelta(days=180)))
    values, more = set(), None
    for _ in range(8):
        data = request('https://paper-api.alpaca.markets/v2/options/contracts', params)
        values.update(c['expiration_date'] for c in data.get('option_contracts', []))
        more = data.get('next_page_token')
        if not more:
            break
        params['page_token'] = more
    return dict(expiries=sorted(values), partial=bool(more), scope='Listed expiries within 180 days; bounded metadata scan')
