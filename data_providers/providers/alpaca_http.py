"""Paced Alpaca JSON transport with a browser TLS handshake and bounded retries."""
import threading
import time
from curl_cffi import requests
from curl_cffi.const import CurlHttpVersion
from data_providers.shared.net import note_headers, throttle

_local = threading.local()


def get_json(url, headers, tries=4, base=1.5):
    if tries < 1:
        raise ValueError('tries must be positive')
    session = getattr(_local, 'session', None)
    if session is None:
        session = _local.session = requests.Session(
            impersonate='chrome', http_version=CurlHttpVersion.V1_1)
    for attempt in range(tries):
        throttle('alpaca')
        try:
            response = session.get(url, headers=headers, timeout=30)
        except requests.exceptions.RequestException:
            if attempt == tries - 1:
                raise
        else:
            note_headers('alpaca', response.headers)
            # An entitlement/authentication failure cannot recover through immediate retries.
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                response.raise_for_status()
                return response.json()
        time.sleep(base ** attempt)


def utc_day_bounds(start, end):
    """Date-only inclusive windows cover the final day's session; timestamps stay exact."""
    start, end = str(start), str(end)
    return (start+'T00:00:00Z' if len(start)==10 else start,
            end+'T23:59:59Z' if len(end)==10 else end)
