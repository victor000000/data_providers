"""CFE public contract archives; keep source bytes and separate settlements from trades."""
import csv
import hashlib
import io
import re
import threading
import time
from datetime import date
from urllib.parse import urlparse

import requests

from data_providers.jobs.history.futures_store import number

API = 'https://www-api.cboe.com/us/futures/market_statistics/historical_data/'
CDN = 'https://cdn.cboe.com/'


class CFEArchive:
    def __init__(self, folder):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.local = threading.local()

    def get(self, url):
        if urlparse(url).netloc not in {'www-api.cboe.com', 'cdn.cboe.com'} or urlparse(url).scheme != 'https':
            raise ValueError('Invalid CFE archive URL')
        if not hasattr(self.local, 'session'):
            self.local.session = requests.Session()
        for attempt in range(3):
            try:
                response = self.local.session.get(url, timeout=(10, 45))
                digest = hashlib.sha256(url.encode()).hexdigest()+'-'+hashlib.sha256(response.content).hexdigest()
                (self.folder/(digest+'.raw')).write_bytes(response.content)
                (self.folder/(digest+'.url')).write_text(url)
                response.raise_for_status()
                return response
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2**attempt)

    def listing(self, path):
        return self.get(API+path).json()

    def rows(self, item):
        path = item['path']
        if not path.startswith('data/us/futures/market_statistics/historical_data/') or '..' in path:
            raise ValueError('Invalid CFE CSV path')
        response = self.get(CDN+path)
        rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        if rows and 'Trade Date' not in rows[0]:
            raise ValueError('Unexpected CFE archive schema')
        return rows


def future_identity(item):
    expiration = date.fromisoformat(item['expire_date'])
    display = item['product_display'].replace('+VXT', '')
    if not re.fullmatch(r'VX\d*/[FGHJKMNQUVXZ]\d{1,2}', display):
        raise ValueError('Unexpected VX contract identity')
    symbol = display.replace('/', '')
    return 'CFE:'+symbol+':'+str(expiration), symbol, expiration


def option_identity(item):
    match = re.fullmatch(r'(UX\w*/[FGHJKMNQUVXZ]\d{1,2}) ([CP])(\d+)', item['product_display'])
    if not match or item.get('futures_root') != 'UX':
        raise ValueError('Unexpected VX option identity')
    # CFE CSV/vendor display strikes are quoted in hundredths (C1500 = C15).
    return 'CFE:UX:'+item['id'], date.fromisoformat(item['expire_date']), int(match[3])/100, match[2]


def values(row):
    volume = number(row.get('Total Volume'))
    prices = [number(row.get(k)) for k in ('Open', 'High', 'Low', 'Close')]
    if not volume:
        # Zero-trade files contain indicative ranges (sometimes High < Low).
        # They are not executed OHLC. Preserve original bytes, settlement and OI.
        prices = [None]*4
    elif any(x is None or x < 0 for x in prices) or prices[1] < max(prices[0], prices[2], prices[3]) or prices[2] > min(prices[0], prices[3]):
        raise ValueError('Invalid executed CFE OHLC')
    return prices, number(row.get('Settle')), volume, number(row.get('Open Interest'))
