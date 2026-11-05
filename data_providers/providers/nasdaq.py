"""Nasdaq's public daily-bar endpoint (api.nasdaq.com) — no key, ~76ms median.

WHY THIS EXISTS. yfinance is the price warehouse's only feeder, and on 2026-10-10 it returned
YFRateLimitError for a full day. That froze every price CSV at 2026-10-05, which is not
cosmetic: apps/screener/download_atm_iv.py drops any bar whose day has no spot close
(`day not in px`), so the whole US IV series stopped advancing behind an unrelated outage.

  * no API key, and the values agree with Eastmoney's kline endpoint to the cent, so the two
    cross-validate each other.
  * covers stocks, etf, index and mutualfund. The assetclass is a path parameter, so the
    caller cannot know it in advance -- each is tried in turn.
  * `close` is the AS-TRADED close, which is what the warehouse's data/raw CSV holds -- but
    only up to the last corporate action. Nasdaq back-adjusts the bars before one: CTVA's
    pre-spinoff history came back 5.4x smaller than as-traded on 2026-10-09. So a caller
    appending onto a raw series MUST overlap-check first, as 35b_refresh_tail_nasdaq.py does.
  * there is no separate adjusted series here; derive one with the factor, not by assuming.

PITFALLS, both hit on 2026-10-10:
  * a FUTURE `todate` returns an EMPTY table rather than an error, so it looks exactly like a
    delisted symbol. The date is computed here rather than passed in for that reason.
  * numbers arrive as display strings -- "$1,234.56", "37,881,200" -- so float() on the raw
    value raises instead of parsing. A number that is absent is the literal "N/A".
  * first contact from this box often fails with an SSLError before succeeding on retry, so a
    single failed request is not evidence the source is down.
"""
import datetime as dt
import itertools
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ASSETCLASSES = ("stocks", "etf")
TIMEOUT = 25
RETRIES = 4        # TLS resets on first contact are routine here, not an outage
BACK_DAYS = 45     # history span requested; the overlap check only needs the last few bars


def iso(mmddyyyy):
    """Nasdaq's 'MM/DD/YYYY' -> 'YYYY-MM-DD'."""
    m, d, y = mmddyyyy.split("/")
    return f"{y}-{m}-{d}"


def num(row, key):
    """Nasdaq's display strings -> float, or None for a literal 'N/A'.

    '$1,234.56' and '37,881,200' both appear, and so does 'N/A' -- found mid-run over the
    universe, where it raised ValueError inside a worker thread and killed the sweep.
    """
    try:
        return float(str(row[key]).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def bars(row):
    """One tradesTable row -> (iso date, (open, high, low, close, volume)), or None.

    A row missing any PRICE is not a bar and must not be appended -- appending it would put a
    zero in the warehouse. A missing VOLUME is different: it means no trading, which is a real
    0 rather than an unknown, and the schema takes it.
    """
    prices = [num(row, k) for k in ("open", "high", "low", "close")]
    if any(p is None for p in prices):
        return None
    vol = num(row, "volume")
    return iso(row["date"]), (*prices, 0.0 if vol is None else vol)


def daily_bars(symbol, back_days=BACK_DAYS):
    """{ISO date: (open, high, low, close, volume)} for one symbol, or {} if it has none.

    {} is ambiguous by construction -- a delisted symbol and an unreachable API both land
    there -- so callers must report it rather than read it as "no new data".
    """
    today = dt.date.today()
    frm = (today - dt.timedelta(days=back_days)).isoformat()
    # Nasdaq spells a share class with a dot (BRK.B) where yfinance uses a dash (BRK-B), and
    # the dash form answers `data: null` rather than an error -- so both are always tried.
    spellings = (symbol, symbol.replace("-", ".")) if "-" in symbol else (symbol,)
    for name, assetclass in itertools.product(spellings, ASSETCLASSES):
        url = (f"https://api.nasdaq.com/api/quote/{name}/historical"
               f"?assetclass={assetclass}&fromdate={frm}&todate={today.isoformat()}&limit=100")
        for _attempt in range(RETRIES):
            try:
                body = requests.get(url, headers=UA, timeout=TIMEOUT).json()
                rows = (body.get("data") or {}).get("tradesTable", {}).get("rows", [])
            except Exception:                    # noqa: BLE001 -- retry, then try next class
                time.sleep(2)
                continue
            got = dict(b for b in map(bars, rows) if b)
            if got:
                return got
            break                                # answered, but no usable rows
    return {}
