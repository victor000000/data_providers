"""Bounded source checks; HTTP reachability never implies data entitlement."""
import os
import socket
import requests
from .state import now
# Includes acquisition adapters and underlying exchange endpoints. No HK acquisition.
HTTP = {
    'Yahoo': 'https://query1.finance.yahoo.com/v8/finance/chart/SPY',
    'Massive': 'https://api.polygon.io/v2/aggs/ticker/SPY/prev',
    'Tiingo': 'https://api.tiingo.com', 'Nasdaq': 'https://api.nasdaq.com',
    'Sina': 'https://stock.finance.sina.com.cn', 'Eastmoney': 'https://push2.eastmoney.com',
    'CZCE': 'https://www.czce.com.cn', 'SHFE': 'https://www.shfe.com.cn',
    'DCE': 'https://www.dce.com.cn', 'CFFEX': 'https://www.cffex.com.cn',
    'INE': 'https://www.ine.cn', 'GFEX': 'https://www.gfex.com.cn',
    'SSE': 'https://www.sse.com.cn', 'SZSE': 'https://www.szse.cn',
    'CBOE/CFE': 'https://www.cboe.com',
}
SOURCES = ['IBKR Gateway', 'Futu OpenD', 'ThetaData', 'Alpaca paper',
           'Alpaca acquisition', *HTTP, 'Trading exports']


def http_check(url, headers=None):
    with requests.get(url, headers=headers or {'User-Agent':'Mozilla/5.0'},
                      timeout=(3,6), allow_redirects=False, stream=True) as response:
        code = response.status_code
    return dict(transport='reachable', access='response received' if code == 200 else 'not verified',
                detail=f'HTTP {code}; endpoint response only, no quote entitlement or freshness proof',
                http_status=code, quote_status='not checked')


def alpaca(paper):
    if paper:
        from ..providers.us_option_data import headers
        auth = headers()
    else:
        from ..credentials import alpaca_headers
        auth = alpaca_headers()
    # This request exercises OPRA access without placing orders or acquiring a backfill.
    result = http_check('https://data.alpaca.markets/v1beta1/options/snapshots/SPY?feed=opra&limit=1', auth)
    code = result['http_status']
    result.update(access='OPRA endpoint accepted' if code == 200 else 'denied' if code in (401,403) else 'not verified',
                  detail=f'OPRA HTTP {code}; quote freshness must be checked on the selected contract')
    return result


def local_check(name):
    if name == 'IBKR Gateway':
        import asyncio
        asyncio.set_event_loop(asyncio.new_event_loop())
        from ib_async import IB, StartupFetch
        ib = IB()
        ib.RequestTimeout = 6
        try:
            ib.connect(os.getenv('IB_HOST','127.0.0.1'), int(os.getenv('IB_PORT','4001')),
                       clientId=193, timeout=6, readonly=True, fetchFields=StartupFetch(0))
            clock = ib.reqCurrentTime()
            return dict(transport='reachable', access='API handshake',
                        detail='Read-only server time: '+str(clock), quote_status='not checked')
        finally:
            ib.disconnect()
            asyncio.get_event_loop().close()
    if name == 'Futu OpenD':
        # Bounded socket probe: separate watcher protocol evidence is displayed in status.
        with socket.create_connection((os.getenv('FUTU_HOST','127.0.0.1'),11111),timeout=3):
            return dict(transport='reachable', access='TCP only', quote_status='not checked',
                        detail='OpenD port accepts connections; login/quote entitlements not verified')
    from ..providers.thetadata import ThetaDataClient
    ok = ThetaDataClient().terminal_available(timeout=6)
    return dict(transport='reachable' if ok else 'unavailable', access='EOD endpoint' if ok else 'not verified',
                quote_status='not checked', detail='Theta v3 EOD probe only; option entitlement and freshness are checked on Option chain')
