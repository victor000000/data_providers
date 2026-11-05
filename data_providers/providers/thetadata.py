"""ThetaData v3 client for published EOD data through an authenticated terminal.

Endpoint precedence: explicit constructor argument, THETADATA_BASE_URL,
secrets/thetadata.json base_url, then http://127.0.0.1:25503/v3.
The terminal needs Java21+ and can run natively on Linux/WSL or on Windows.
Credentials come from the constructor, THETADATA_API_KEY or the same settings file.

Product availability follows the terminal's entitlements; this client does not
infer access from another product's subscription. EOD reports are generated around17:15ET;
created is the report time and last_trade is the last trade time. Preserve those
vendor fields in captures and keep this source separate from live quotes.
Docs: https://thetadata.net/docs/operations/stock_history_eod.html
"""
import csv as _csv
import datetime as dt
import io
import json
import os
import time
import ipaddress
from urllib.parse import urlsplit
from typing import Optional, List, Dict, Any
import requests

DEFAULT_BASE_URL = os.environ.get("THETADATA_BASE_URL", "http://127.0.0.1:25503/v3")


def _loopback_endpoint(url: str) -> bool:
    """Recognise literal loopback endpoints without DNS or a network probe."""
    try:
        host = (urlsplit(url).hostname or '').rstrip('.').lower()
        return host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _settings():
    try:
        from data_providers.runtime import ROOT
        path = ROOT / 'secrets' / 'thetadata.json'
        with open(path) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_api_key() -> Optional[str]:
    return os.environ.get("THETADATA_API_KEY") or _settings().get('api_key')


class ThetaDataClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or load_api_key()
        self.base_url = (base_url or os.environ.get('THETADATA_BASE_URL') or
                         _settings().get('base_url') or DEFAULT_BASE_URL).rstrip('/')
        self.session = requests.Session()
        # Terminal authentication is upstream. Local HTTP must not inherit a
        # system proxy or .netrc credentials when NO_PROXY is absent/incomplete.
        # Explicit remote terminals retain their existing environment behavior.
        if _loopback_endpoint(self.base_url):
            self.session.trust_env = False
        adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=4,
                                               pool_block=True, max_retries=0)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({"Accept": "application/json"})
        self._last_request_time = 0
        # Retain conservative spacing. Entitlements differ by product; request
        # reservations and the shared history lock remain owned by ingestion.
        self._min_interval = 0.5

    def _rate_limit(self):
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    def terminal_available(self, timeout: float = 5.0) -> bool:
        """Is the local Theta Terminal reachable AND serving v3? Never raises.

        Neither the root nor a bare /v3/ can answer this, which is why this took
        two attempts. Against a live terminal:

            GET /            -> 410 "We have upgraded to API v3"   (proves nothing
                                 about v3 being served at THIS base_url)
            GET /v3/         -> 404  (no directory listing)
            GET /v3/stock/history/eod?...  -> 200

        So `< 500` called a wrong address reachable (404 counts), and tightening to
        `== 200` on the ROOT called a perfectly healthy terminal unreachable. The
        only honest probe is a real v3 endpoint; the result is discarded, we just
        need it to have answered as v3. One small request, cached by date server-side.
        """
        try:
            # Ask for a window that certainly has data rather than for today: this
            # runs pre-market, when today's EOD does not exist yet and the terminal
            # answers 472 "No data found for your request" -- which is a perfectly
            # healthy terminal saying it has nothing for that date, not an outage.
            end = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
            start = (dt.date.today() - dt.timedelta(days=10)).strftime("%Y%m%d")
            r = self.session.get(f"{self.base_url}/stock/history/eod",
                                 params={'symbol': 'AAPL', 'start_date': start,
                                         'end_date': end},
                                 timeout=timeout)
            # 200 = answered with data. 472 = answered, nothing for that window (a
            # terminal with no AAPL history at all). Both prove a v3 terminal is
            # serving this base_url; a wrong address gives 404 and is rejected.
            return r.status_code in (200, 472)
        except Exception:
            return False

    # v3 CSV requests use dollar strikes and explicit calendar dates.

    def _request_csv(self, endpoint: str, params: Dict[str, Any]) -> List[Dict[str, str]]:
        """GET a v3 endpoint and parse its CSV body into dicts. [] on failure."""
        self._rate_limit()
        # The v3 terminal authenticates upstream; its private key is not a query parameter.
        try:
            r = self.session.get(f"{self.base_url}/{endpoint}", params=params, timeout=60)
        except Exception as e:
            print(f"ThetaData {endpoint} transport error: {type(e).__name__}")
            return []

        body = r.text or ""
        if r.status_code != 200:
            # A FREE-tier refusal arrives as a 4xx with a human sentence.
            print(f"ThetaData {endpoint} -> {r.status_code}: {body.strip()[:160]}")
            return []
        if "requiring a value subscription" in body:
            print(f"ThetaData {endpoint}: needs a paid subscription")
            return []

        return [row for row in _csv.DictReader(io.StringIO(body))]

    @staticmethod
    def _f(v):
        """CSV value -> float or None. ThetaData pads missing fields with ''."""
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def get_stock_eod(self, symbol: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Daily OHLC for stock/ETF. Dates are YYYYMMDD.

        One stock symbol per request; canonical class shares use the provider map.
        """
        from data_providers.data.symbols import canonical_for, for_source, SymbolError
        try:
            feed_symbol = for_source(canonical_for('yahoo',symbol) or symbol,'thetadata_stock')
        except SymbolError:
            feed_symbol = symbol
        rows = self._request_csv("stock/history/eod", {
            'symbol': feed_symbol, 'start_date': start_date, 'end_date': end_date,
        })
        return [{
            'symbol': symbol,
            'date': (r.get('created') or '')[:10],
            'open': self._f(r.get('open')), 'high': self._f(r.get('high')),
            'low': self._f(r.get('low')), 'close': self._f(r.get('close')),
            'volume': self._f(r.get('volume')),
            'bid': self._f(r.get('bid')), 'ask': self._f(r.get('ask')),
        } for r in rows]

    def get_option_eod(self, symbol: str, expiration: str, strike: float, right: str,
                       start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Daily OHLC for ONE option contract.

        strike is dollars; expiration is ISO (YYYY-MM-DD); right is C or P.
        This is a REAL traded price -- the thing the Black-Scholes re-pricing
        in the risk profile is not.
        """
        rows = self._request_csv("option/history/eod", {
            'symbol': symbol, 'expiration': expiration, 'strike': strike,
            'right': right.upper(), 'start_date': start_date, 'end_date': end_date,
        })
        return [{
            'symbol': r.get('symbol', symbol),
            'expiration': r.get('expiration'), 'strike': self._f(r.get('strike')),
            'right': r.get('right'), 'date': (r.get('created') or '')[:10],
            'open': self._f(r.get('open')), 'high': self._f(r.get('high')),
            'low': self._f(r.get('low')), 'close': self._f(r.get('close')),
            'volume': self._f(r.get('volume')),
            'bid': self._f(r.get('bid')), 'ask': self._f(r.get('ask')),
        } for r in rows]


# Singleton instance
_client = None


def get_client() -> ThetaDataClient:
    """Get or create ThetaData client singleton."""
    global _client
    if _client is None:
        _client = ThetaDataClient()
    return _client


if __name__ == "__main__":
    c = get_client()
    print("base_url:", c.base_url)
    print("terminal_available:", c.terminal_available())
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=5)).strftime("%Y%m%d")
    print("\nTLT EOD:")
    for row in c.get_stock_eod("TLT", start, end):
        print(" ", row)
    print("\nTLT 2026-11-20 74P EOD:")
    for row in c.get_option_eod("TLT", "2026-11-20", 74.0, "P", start, end):
        print(" ", row)
