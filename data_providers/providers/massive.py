"""Massive (ex-Polygon.io) client — free tier, REST API.

CREDENTIALS: the S3 keys from massive.com/dashboard/keys. Despite the docs
saying S3 keys "cannot be used in place" of a REST key, the secret access key
authenticates fine as the `apiKey` query parameter (verified 2026-10-05), so
one credential file covers both. Read from MASSIVE_API_KEY or
secrets/massive.json.

TIER — what works and what does not (verified 2026-10-05):

  WORKS
    /v2/aggs/grouped/locale/us/market/stocks/{date}  -- the whole US market in
        ONE request (12,601 tickers on 2026-10-02). This is the useful one: it
        sidesteps the rate limit entirely for any number of underlyings.
    /v2/aggs/ticker/{ticker}/prev                   -- one symbol's last close
    /v2/aggs/ticker/O:.../range/...                 -- per-contract option bars

  BLOCKED
    S3 flatfiles (files.massive.com)  -- 403. The free tier lists the bucket
        but cannot read a single object; flat files are paid-only. The catalog
        advertises options day-aggs back to 2014, which would be the whole
        backfill, but none of it is downloadable here.
    /v2/snapshot/...                  -- 403 NOT_AUTHORIZED, needs a paid plan.
        So there are NO live quotes from this source.
    options via /grouped/             -- marketType accepts only stocks/fx/crypto.

Net: Massive is a third END-OF-DAY source. It is not a live feed, and cannot
be one on this tier.

RATE LIMIT: 5 requests/minute on the free tier. The grouped endpoint costs one
request for the entire market; per-contract option bars are the expensive path
and are cached per (contract, date) so a re-run the same day costs nothing.
"""
import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests

BASE = "https://api.polygon.io"


def load_api_key() -> Optional[str]:
    """Env var first, then secrets/massive.json."""
    key = os.environ.get("MASSIVE_API_KEY")
    if key:
        return key
    try:
        from data_providers.runtime import ROOT as root
        return json.loads((root / "secrets" / "massive.json").read_text()).get("secret_access_key")
    except Exception:
        return None


class MassiveClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or load_api_key()
        self.session = requests.Session()
        self._last = 0.0
        self._min_interval = 12.5   # 5 req/min, with headroom

    def _throttle(self):
        wait = self._min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _get(self, path: str, params: Dict[str, Any] = None) -> Optional[dict]:
        if not self.api_key:
            print("Massive: no API key (set MASSIVE_API_KEY or secrets/massive.json)")
            return None
        self._throttle()
        p = dict(params or {})
        p['apiKey'] = self.api_key
        try:
            r = self.session.get(f"{BASE}{path}", params=p, timeout=60)
        except Exception as e:
            print(f"Massive transport error: {type(e).__name__}")
            return None
        if r.status_code == 403:
            print(f"Massive {path}: not entitled on this plan")
            return None
        if r.status_code == 429:
            print(f"Massive {path}: rate limited")
            return None
        if r.status_code != 200:
            print(f"Massive {path} -> {r.status_code}: {r.text[:140]}")
            return None
        return r.json()

    def grouped_stocks(self, date: str) -> Dict[str, Dict[str, float]]:
        """Every US ticker's OHLCV for one session, in ONE request.

        date is YYYY-MM-DD. Returns {ticker: {'close':..,'open':..,...}}.
        This is the efficient path: the rate limit stops mattering because the
        whole market arrives at once.
        """
        data = self._get(f"/v2/aggs/grouped/locale/us/market/stocks/{date}")
        if not data or data.get('status') not in ('OK', 'DELAYED'):
            return {}
        out = {}
        for r in data.get('results') or []:
            t = r.get('T')
            if t:
                out[t] = {'close': r.get('c'), 'open': r.get('o'),
                          'high': r.get('h'), 'low': r.get('l'),
                          'volume': r.get('v'), 'vwap': r.get('vw')}
        return out

    def option_daily(self, occ_symbol: str, start: str, end: str) -> List[Dict[str, Any]]:
        """Daily bars for one option contract.

        occ_symbol is the OCC form with the O: prefix, e.g.
        O:TLT261120P00074000. start/end are YYYY-MM-DD.
        """
        data = self._get(f"/v2/aggs/ticker/{occ_symbol}/range/1/day/{start}/{end}")
        if not data or data.get('status') not in ('OK', 'DELAYED'):
            return []
        return [{'date': dt.datetime.utcfromtimestamp(r['t'] / 1000).date().isoformat(),
                 'open': r.get('o'), 'high': r.get('h'), 'low': r.get('l'),
                 'close': r.get('c'), 'volume': r.get('v'), 'vwap': r.get('vw')}
                for r in data.get('results') or []]


def occ_symbol(underlying: str, expiration, strike: float, call_put: str) -> str:
    """Build an OCC option symbol: O:TLT261120P00074000.

    Format is O:<root><YYMMDD><C|P><strike * 1000, 8 digits, zero-padded>.
    """
    exp = expiration
    if hasattr(exp, 'strftime'):
        ymd = exp.strftime('%y%m%d')
    else:
        ymd = dt.date.fromisoformat(str(exp)[:10]).strftime('%y%m%d')
    return f"O:{underlying}{ymd}{call_put.upper()}{int(round(strike * 1000)):08d}"


_client = None


def get_client() -> MassiveClient:
    global _client
    if _client is None:
        _client = MassiveClient()
    return _client


if __name__ == "__main__":
    c = get_client()
    print("key present:", bool(c.api_key))
    print("occ:", occ_symbol("TLT", "2026-11-20", 74.0, "P"))
    # Cheap check first: the grouped endpoint costs a single request.
    print("\nsample grouped closes:")
    g = c.grouped_stocks("2026-10-02")
    print("  tickers returned:", len(g))
    for t in ("TLT", "EWZ", "META", "CRWD", "SWKS"):
        print(f"  {t}: {g.get(t, {}).get('close')}")
