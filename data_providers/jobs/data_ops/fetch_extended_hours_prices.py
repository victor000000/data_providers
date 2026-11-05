import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

# ── --help guard ─────────────────────────────────────────────────────────────
# This script WRITES to a database and takes no arguments, so `script.py --help`
# used to execute it in full. Not hypothetical: a 2026-10-05 sweep ran every app
# with --help and rewrote the fills table (via seed_fills.py) and risk_profile.
# A read-only-looking flag must not be a write. Exits before any connection.
from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap below, and a
# docstring must be the first statement, so all 33 copies printed "(no docstring)".

"""fetch_extended_hours_prices.py — fetch pre/after/overnight prices from all sources and persist to DB.

This is the STANDARD procedure for price data collection:
1. Fetch from Alpaca, TWS, yfinance, Futu (all sources, all hours)
2. Mark session type: RTH, pre-market, after-hours, overnight
3. Save to market_history.extended_hours_quotes table
4. Use for risk analysis when positions table is stale

All timestamps in UTC throughout.

Run: uv run python apps/data_ops/fetch_extended_hours_prices.py
"""
import datetime as dt
from zoneinfo import ZoneInfo
import os
import socket
from ib_async import IB, Stock

# `clock.now()`, not `datetime.utcnow()`. Both return naive UTC, so this is not a
# behaviour change -- but utcnow() is deprecated and scheduled for removal, and this file
# WRITES the timestamps every downstream freshness check compares against. clock.py is the
# repo's one definition of "now", written because a bare now() on this box (Asia/Shanghai)
# is eight hours from the stamps it is compared with.
from data_providers.data import clock
from data_providers.data import db
from data_providers.providers import ibmarks
from data_providers.providers import tws
from data_providers.providers import source_rank

import yfinance as yf

try:
    from futu import OpenQuoteContext
    FUTU_AVAILABLE = True
except ImportError:
    FUTU_AVAILABLE = False

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False

try:
    from data_providers.providers.thetadata import get_client as get_theta_client
    THETA_AVAILABLE = True
except ImportError:
    THETA_AVAILABLE = False

TIINGO_BASE = "https://api.tiingo.com"


def load_tiingo_key():
    """Tiingo token: env var first, then secrets/tiingo.json.

    Kept out of the source tree deliberately -- secrets/ is gitignored, and a
    key committed once stays in history.
    """
    key = os.environ.get('TIINGO_API_KEY')
    if key:
        return key
    path = bootstrap.ROOT / "secrets" / "tiingo.json"
    try:
        import json
        return json.loads(path.read_text()).get('api_key')
    except Exception:
        return None


def load_alpaca_keys():
    """Alpaca key/secret: env vars first, then secrets/alpaca.json. Same rule as Tiingo's.

    THE BUG THIS FIXES. This source read `os.environ` only, so it returned zero quotes on
    every run and still reported itself as "configured" -- while secrets/alpaca.json sat
    there with working keys. Measured 2026-10-06: with those keys the API answers fine.
    A source that is silently absent is worse than one that is known to be down, because
    nothing in the run says the book was priced without it.

    Note the keys in the file are PAPER keys, which is the right default for market data
    (the data API is not account-scoped) and keeps a live-trading credential off this box.
    """
    key = os.environ.get('ALPACA_API_KEY')
    secret = os.environ.get('ALPACA_SECRET_KEY')
    if key and secret:
        return key, secret
    path = bootstrap.ROOT / "secrets" / "alpaca.json"
    try:
        import json
        d = json.loads(path.read_text())
        return d.get('key_id'), d.get('secret')
    except Exception:
        return None, None


def get_session_type(utc_time):
    """Determine market session: RTH, pre-market, after-hours, or overnight.

    Uses a real timezone, not a fixed offset. The previous version subtracted a
    flat 5 hours with the comment "ignoring DST", so through the whole of EDT --
    which is most of the trading year -- every session label was an hour early:
    13:30 UTC (09:30 ET, the open) was filed as pre-market, and 20:00 UTC (16:00
    ET, the close) as RTH. session_type is written into extended_hours_quotes and
    printed by the risk profile, so the label was wrong, not just the boundary.

    Also splits at 09:30 rather than 09:00, which is when RTH actually opens.
    """
    if utc_time.tzinfo is None:
        utc_time = utc_time.replace(tzinfo=dt.timezone.utc)
    et = utc_time.astimezone(ZoneInfo("America/New_York"))
    mins = et.hour * 60 + et.minute

    if 9 * 60 + 30 <= mins < 16 * 60:
        return 'RTH'
    if 4 * 60 <= mins < 9 * 60 + 30:
        return 'pre-market'
    if 16 * 60 <= mins < 20 * 60:
        return 'after-hours'
    return 'overnight'


def fetch_alpaca(tickers):
    """Fetch from Alpaca."""
    results = []
    if not ALPACA_AVAILABLE:
        return results

    try:
        api_key, api_secret = load_alpaca_keys()
        if not api_key or not api_secret:
            print("   Alpaca: no credentials in env or secrets/alpaca.json — skipping")
            return results

        client = StockHistoricalDataClient(api_key, api_secret)
        request = StockLatestQuoteRequest(symbol_or_symbols=tickers)
        quotes = client.get_stock_latest_quote(request)

        for ticker, quote in quotes.items():
            if quote.bid_price and quote.ask_price and quote.bid_price > 0 and quote.ask_price > 0:
                mid = (quote.bid_price + quote.ask_price) / 2
                results.append({
                    'ticker': ticker,
                    'bid': float(quote.bid_price),
                    'ask': float(quote.ask_price),
                    'mid': float(mid),
                    'last': None,
                    'source': 'Alpaca',
                    'timestamp': quote.timestamp.replace(tzinfo=None) if hasattr(quote, 'timestamp') else clock.now()
                })
    except Exception as e:
        print(f"Alpaca error: {repr(e)[:200]}")

    return results


def fetch_tws(tickers):
    """Fetch from TWS."""
    results = []
    try:
        ib = IB()
        ib.RequestTimeout = 45
        tws.connect(ib, client_id=113, timeout=25)
        n = ibmarks.clean

        stock_pairs = list(zip(tickers, ib.qualifyContracts(*[Stock(s, "SMART", "USD") for s in tickers])))
        stock_ok = [(s, c) for s, c in stock_pairs if c]

        now = clock.now()

        def snapshot():
            out = []
            for (ticker, _), t in zip(stock_ok, ib.reqTickers(*[c for _, c in stock_ok])):
                bid, ask, last = n(t.bid), n(t.ask), n(t.last)
                if bid or ask or last:
                    out.append({
                        'ticker': ticker,
                        'bid': bid,
                        'ask': ask,
                        'mid': (bid + ask) / 2 if (bid and ask) else None,
                        'last': last,
                        'source': 'TWS',
                        'timestamp': now
                    })
            return out

        if stock_ok:
            results = snapshot()
            if not results:
                # CONNECTED BUT SILENT, and that is not the same as down. The handshake
                # succeeds while the gateway is still logging in or warming up, and
                # reqTickers then returns empty tickers for every contract with no error
                # at all -- measured 2026-10-06 08:36 UTC, minutes before the same call
                # returned EWZ 43.05/43.21 and TLT 77.51/77.54. Accepting that empty
                # result silently downgraded every spot in the book from the broker's own
                # book to a last-traded price, and nothing in the output said why.
                print(f"   TWS: connected to {tws.LAST_PORT['port']} but returned nothing "
                      f"for all {len(stock_ok)} tickers — waiting 5s and retrying once")
                ib.sleep(5)
                results = snapshot()
            if not results:
                print("   !! TWS STILL silent after the retry. TWS is SOURCE_RANK 1 and the "
                      "only source with a genuine two-sided book outside regular hours, so "
                      "the spots below fall back to last-traded prices that are NOT "
                      "real-time. Check that the gateway is logged in.")

        ib.disconnect()
    except Exception as e:
        print(f"TWS error: {repr(e)[:200]}")

    return results


def fetch_yfinance(tickers):
    """Fetch from yfinance."""
    results = []
    now = clock.now()

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            bid = info.get('bid')
            ask = info.get('ask')
            current = info.get('currentPrice') or info.get('regularMarketPrice')

            if bid or ask or current:
                results.append({
                    'ticker': ticker,
                    'bid': float(bid) if bid else None,
                    'ask': float(ask) if ask else None,
                    'mid': (float(bid) + float(ask)) / 2 if (bid and ask) else None,
                    'last': float(current) if current else None,
                    'source': 'yfinance',
                    'timestamp': now
                })
        except Exception as e:
            print(f"yfinance {ticker} error: {repr(e)[:100]}")

    return results


def futu_opend_up(host=os.environ.get("FUTU_HOST", "host.docker.internal" if pathlib.Path("/.dockerenv").exists() else "127.0.0.1"), port=11111, timeout=2.0):
    """Is Futu OpenD actually listening? Probe the socket BEFORE constructing a client.

    OpenQuoteContext's constructor retries the connection forever -- there is no timeout
    and no give-up path -- so a down OpenD does not fail this run, it HANGS it. On
    2026-10-06 the fetch sat at step 4 for 11 minutes while OpenD refused every attempt
    (ECONNREFUSED in py_2026_10_06.log, one per 6s), and Tiingo and ThetaData, which run
    after it, never ran at all. A 2-second probe turns an unbounded hang into a skip.

    Skipping is cheap here and that is why a probe is enough: Futu is SOURCE_RANK 6,
    last, and `last_only` by construction -- it publishes no book, so outside regular
    hours it carries the previous close and nothing else.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def fetch_futu(tickers):
    """Fetch from Futu OpenD."""
    results = []
    if not FUTU_AVAILABLE:
        return results

    if not futu_opend_up():
        # NOT silent: a source that drops out must be visible in the run's output, or a
        # thinner cross-source consensus reads as agreement.
        print("   Futu OpenD not reachable on 11111 — skipping this source "
              "(rank 6, last_only; no book to lose)")
        return results

    try:
        ctx = OpenQuoteContext(host=os.environ.get("FUTU_HOST", "host.docker.internal" if pathlib.Path("/.dockerenv").exists() else "127.0.0.1"), port=11111)
        now = clock.now()

        for ticker in tickers:
            try:
                code = f"US.{ticker}"
                ret, data = ctx.get_market_snapshot([code])
                if ret == 0 and not data.empty:
                    row = data.iloc[0]
                    last_price = row.get('last_price')
                    if last_price and last_price > 0:
                        results.append({
                            'ticker': ticker,
                            'bid': None,
                            'ask': None,
                            'mid': None,
                            'last': float(last_price),
                            'source': 'Futu',
                            'timestamp': now
                        })
            except Exception as e:
                print(f"Futu {ticker} error: {repr(e)[:100]}")

        ctx.close()
    except Exception as e:
        print(f"Futu connection error: {repr(e)[:200]}")

    return results


def fetch_tiingo(tickers):
    """Fetch from Tiingo's IEX feed (one batched request for all tickers).

    What Tiingo is good for depends on the session, and the distinction is the
    same one that broke the TLT analysis:

      * RTH -- returns a real two-sided IEX quote (bidPrice/askPrice), so it
        classifies as a live source.
      * Extended hours -- IEX is closed, so `bidPrice`/`askPrice` come back
        null and only `tngoLast` is populated. tngoLast is the last trade,
        which through the overnight is the regular-session close. It is
        therefore emitted WITHOUT bid/ask and the validator will file it as
        last_only, not as a live price.

    Its EOD endpoint is also a strong independent check on the session close:
    on 2026-10-05 Tiingo, ThetaData and yfinance all gave TLT 2026-10-02 =
    77.48 to the cent.
    """
    import requests

    key = load_tiingo_key()
    if not key:
        print("   Tiingo: no API key (set TIINGO_API_KEY or secrets/tiingo.json)")
        return []

    try:
        r = requests.get(f"{TIINGO_BASE}/iex/", params={
            'tickers': ",".join(tickers), 'token': key,
        }, timeout=30)
        if r.status_code != 200:
            print(f"   Tiingo: HTTP {r.status_code} {r.text[:120]}")
            return []
        payload = r.json()
    except Exception as e:
        print(f"   Tiingo error: {repr(e)[:150]}")
        return []

    now = clock.now()
    results = []
    for q in payload:
        ticker = q.get('ticker')
        if not ticker:
            continue
        bid, ask = q.get('bidPrice'), q.get('askPrice')
        last = q.get('tngoLast') or q.get('last')
        mid = (bid + ask) / 2 if (bid and ask and bid > 0 and ask > 0) else None
        if not (mid or last):
            continue
        results.append({
            'ticker': ticker,
            'bid': bid if bid else None,
            'ask': ask if ask else None,
            'mid': mid,
            'last': last,
            'source': 'Tiingo',
            'timestamp': now,
        })
    return results


def fetch_thetadata(tickers):
    """ThetaData contributes no LIVE quotes, by subscription tier.

    Deliberately returns []. The free tier serves only `history/eod`; every
    live endpoint (`/snapshot/`, `history/quote`) answers "requiring a value
    subscription", and FPSS streaming refuses free accounts outright. So there
    is no configuration in which this function can produce an extended-hours
    quote, and pretending otherwise would put a source in the list that can
    only ever contribute nothing.

    ThetaData's real contribution is end-of-day -- the session close and the
    traded option close -- which complete_risk_analysis.py pulls via
    fetch_theta_eod() for the snapshot side of the comparison.
    """
    if THETA_AVAILABLE:
        try:
            from data_providers.providers.thetadata import get_client
            c = get_client()
            state = "reachable" if c.terminal_available() else "unreachable"
            print(f"   ThetaData: terminal {state} at {c.base_url}; "
                  f"EOD only on this tier (no live quotes) -- see complete_risk_analysis.py")
        except Exception as e:
            print(f"   ThetaData: {repr(e)[:100]}")
    else:
        print("   ThetaData: client not importable")
    return []


# Source quality, best first. Used only when the sources disagree and there is
# no majority to arbitrate: TWS is a direct broker feed with a real exchange
# book, Alpaca is a live consolidated quote, yfinance is scraped and is the
# source that produced the bogus TLT print, Futu serves snapshot data with no
# book at all.
# Source quality, best first. Used only when the sources disagree and there is
# no majority to arbitrate. Tiingo sits with TWS: its IEX feed is a real
# exchange quotation, and it agreed with ThetaData and yfinance on the TLT
# close to the cent. yfinance is lower because it is the source that produced
# the bogus TLT print, and Futu serves snapshot data with no book at all.
SOURCE_RANK = source_rank.SOURCE_RANK

# How old a two-sided quote may be and still count as real-time. Matches the freshness
# window spot_for() uses to rank quotes, so "real-time" means one thing in both places.
LIVE_MAX_AGE = dt.timedelta(minutes=source_rank.LIVE_MAX_AGE_MINUTES)


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def validate_prices(all_quotes, rejections=None):
    """Cross-check every source against the consensus for the same ticker.

    THE BUG THIS EXISTS TO CATCH. On 2026-10-05 yfinance returned TLT at
    82.92/82.97 while TWS returned 77.62/77.69 and TLT's last close was 77.48.
    Taking that mid as "the overnight price" turned a +0.2% move into +7.1% and
    fabricated a -454% loss on the 82C leg. A pure magnitude threshold cannot
    catch it: EWZ genuinely moved +9.7% the same night, so any threshold loose
    enough to admit the real move also admits the fake one.

    The signal that does separate them is AGREEMENT. Sources quoting the same
    instrument at the same instant must see the same price; EWZ's sources agree
    with each other and TLT's do not. So: build a consensus and reject the
    sources that disagree with it, rather than judging any single price on size.

    Quote type matters too, and is handled first. A two-sided quote (bid AND
    ask) is a live market; a bare `last` is a trade that may be hours stale --
    Futu returns TLT's RTH close of 77.48 all through the overnight session.
    Those are different quantities, so they are never compared against each
    other, and last-only rows are kept but flagged is_realtime=False so the
    risk profile can decline to call a stale close a live price.
    """
    rejections = rejections if rejections is not None else []

    by_ticker = {}
    for q in all_quotes:
        by_ticker.setdefault(q['ticker'], []).append(q)

    validated = []

    for ticker, quotes in sorted(by_ticker.items()):
        # A live quote needs a real two-sided market. bid>0 AND ask>0 AND ask>=bid:
        # a crossed or zero-sided book is a data fault, not a price.
        live = [q for q in quotes
                if q.get('bid') and q.get('ask') and q['ask'] >= q['bid'] > 0
                and q.get('mid') and q['mid'] > 0]
        last_only = [q for q in quotes if q not in live]

        if not live:
            # Nothing but last-trades. Keep them, marked as not real-time.
            for q in last_only:
                q['is_realtime'] = False
                q['quote_class'] = 'last_only'
                validated.append(q)
            continue

        # Tolerance scales with the book actually being quoted: a name trading
        # 1% wide overnight cannot be held to a 20bp standard. 3x the median
        # relative spread, floored at 2%, so ordinary extended-hours width never
        # trips it while a wrong-moment quote (TLT: 6.8% off) does.
        spreads = [(q['ask'] - q['bid']) / q['mid'] for q in live if q['mid'] > 0]
        tol = max(0.02, 3 * _median(spreads)) if spreads else 0.02

        def _accept(q, cls='live'):
            # A TWO-SIDED QUOTE IS NOT AUTOMATICALLY A CURRENT ONE. Every source except
            # Alpaca stamps its rows with the time WE fetched them, so they cannot look
            # old; Alpaca passes through the VENDOR's timestamp, and its IEX feed will
            # happily carry a quote from the previous session. On 2026-10-06 it offered
            # TLT 77.16/77.22 stamped 2026-10-05 20:48 UTC -- sixteen hours old -- and was
            # accepted here as `live` with is_realtime=True, because this classifier
            # looked at shape and consensus and never at the clock. It lost to TWS on
            # spot_for's freshness ordering, so the book came out right by luck rather
            # than by rule. is_realtime has to mean "current", not just "well-formed".
            ts = q.get('timestamp')
            if ts is not None:
                if ts.tzinfo is not None:
                    ts = ts.astimezone(dt.timezone.utc).replace(tzinfo=None)
                age = clock.now() - ts
                if age > LIVE_MAX_AGE:
                    q['is_realtime'] = False
                    q['quote_class'] = 'live_stale'
                    validated.append(q)
                    print(f"  {q['source']} {q['ticker']}: two-sided but "
                          f"{age.total_seconds()/3600:.1f}h old — kept as live_stale, "
                          f"NOT real-time")
                    return
            q['is_realtime'] = True
            q['quote_class'] = cls
            validated.append(q)

        def _reject(q, consensus, reason_prefix):
            dev = abs(q['mid'] - consensus) / consensus if consensus else 0.0
            msg = (f"{reason_prefix} {ticker} {q['source']} mid=${q['mid']:.2f} "
                   f"deviates {dev*100:.1f}% from ${consensus:.2f} "
                   f"(tol {tol*100:.1f}%, n_live={len(live)})")
            print(f"  {msg}")
            rejections.append({
                'ticker': ticker, 'source': q['source'],
                'price': q['mid'], 'consensus': consensus,
                'deviation_pct': dev * 100, 'reason': msg,
            })

        if len(live) == 1:
            # Nothing to cross-check against. Accept, but say so.
            _accept(live[0], 'live_unverified')
            for q in last_only:
                q['is_realtime'] = False
                q['quote_class'] = 'last_only'
                validated.append(q)
            continue

        if len(live) == 2:
            # A median of two is not a consensus -- it is the midpoint BETWEEN
            # two disagreeing quotes, so both sit equidistant from it and a
            # tolerance test rejects both. That happened to TLT: TWS (77.59,
            # correct) and yfinance (82.94) were each 3.3% off the meaningless
            # midpoint of 80.27, so the ticker lost its live quote entirely.
            # With no majority the data cannot say which is right, so fall back
            # to source quality rather than to arithmetic.
            a, b = live
            if abs(a['mid'] - b['mid']) / max(a['mid'], b['mid']) <= tol:
                _accept(a); _accept(b)          # agree: both fine
            else:
                a, b = sorted(live, key=lambda q: SOURCE_RANK.get(q['source'], 99))
                _accept(a)
                _reject(b, a['mid'], 'DISPUTED (kept higher-priority source):')
        else:
            consensus = _median([q['mid'] for q in live])
            for q in live:
                dev = abs(q['mid'] - consensus) / consensus
                if dev <= tol:
                    _accept(q)
                else:
                    _reject(q, consensus, 'REJECT')

        # Last-only rows ride along unrejected but explicitly not real-time.
        for q in last_only:
            q['is_realtime'] = False
            q['quote_class'] = 'last_only'
            validated.append(q)

    return validated


def main():
    con = db.connect()

    # Create extended hours quotes table if not exists
    con.execute("""
        CREATE TABLE IF NOT EXISTS extended_hours_quotes (
            ticker TEXT NOT NULL,
            timestamp_utc TIMESTAMP NOT NULL,
            session_type TEXT NOT NULL,
            bid DOUBLE,
            ask DOUBLE,
            mid DOUBLE,
            last DOUBLE,
            source TEXT NOT NULL,
            PRIMARY KEY (ticker, timestamp_utc, source)
        )
    """)

    # Provenance columns, added after the fact. `is_realtime` is the load-bearing
    # one: without it a reader cannot tell a live two-sided quote from Futu's
    # copy of yesterday's close, and the risk profile would price off the stale
    # one -- the "0.00% spot change on every ticker" bug.
    for col, typ in (("is_realtime", "BOOLEAN"), ("quote_class", "TEXT")):
        try:
            con.execute(f"ALTER TABLE extended_hours_quotes ADD COLUMN {col} {typ}")
        except Exception:
            pass  # already present

    # Rejected quotes are kept, not dropped. A source that starts disagreeing
    # with the others is a fact about the feed worth being able to look up.
    con.execute("""
        CREATE TABLE IF NOT EXISTS quote_rejections (
            rejected_at_utc TIMESTAMP NOT NULL,
            ticker TEXT NOT NULL,
            source TEXT NOT NULL,
            price DOUBLE,
            consensus DOUBLE,
            deviation_pct DOUBLE,
            reason TEXT
        )
    """)

    # Get tickers from current positions
    tickers = [r[0] for r in con.execute("""
        SELECT DISTINCT underlying
        FROM positions
        WHERE qty <> 0 AND sec_type IN ('OPT', 'STOCK')
    """).fetchall()]

    if not tickers:
        print("No positions found")
        con.close()
        return

    now_utc = clock.now()
    session = get_session_type(now_utc)

    print("=" * 100)
    print(f"Fetching Extended Hours Prices - {now_utc:%Y-%m-%d %H:%M:%S} UTC ({session})")
    print("=" * 100)
    print(f"Tickers: {', '.join(tickers)}")
    print()

    # Fetch from all sources
    print("1. Alpaca...")
    alpaca_quotes = fetch_alpaca(tickers)
    print(f"   Got {len(alpaca_quotes)} quotes")

    print("2. TWS...")
    tws_quotes = fetch_tws(tickers)
    print(f"   Got {len(tws_quotes)} quotes")

    print("3. yfinance...")
    yf_quotes = fetch_yfinance(tickers)
    print(f"   Got {len(yf_quotes)} quotes")

    print("4. Futu...")
    futu_quotes = fetch_futu(tickers)
    print(f"   Got {len(futu_quotes)} quotes")

    print("5. Tiingo...")
    tiingo_quotes = fetch_tiingo(tickers)
    print(f"   Got {len(tiingo_quotes)} quotes")

    print("6. ThetaData...")
    theta_quotes = fetch_thetadata(tickers)
    print(f"   Got {len(theta_quotes)} quotes")

    # Combine all quotes
    all_quotes = (alpaca_quotes + tws_quotes + yf_quotes + futu_quotes
                  + tiingo_quotes + theta_quotes)

    # Validate and filter anomalies
    print()
    print("Validating price consistency across sources...")
    rejections = []
    validated_quotes = validate_prices(all_quotes, rejections)

    for r in rejections:
        con.execute("""
            INSERT INTO quote_rejections
            (rejected_at_utc, ticker, source, price, consensus, deviation_pct, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [now_utc, r['ticker'], r['source'], r['price'], r['consensus'],
              r['deviation_pct'], r['reason']])

    if rejections:
        print(f"  {len(rejections)} quote(s) rejected as inconsistent")

    print()
    print(f"Saving {len(validated_quotes)} validated quotes to database...")

    saved = 0
    for q in validated_quotes:
        try:
            con.execute("""
                INSERT INTO extended_hours_quotes
                (ticker, timestamp_utc, session_type, bid, ask, mid, last, source,
                 is_realtime, quote_class)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
            """, [q['ticker'], q['timestamp'], session, q['bid'], q['ask'],
                  q['mid'], q['last'], q['source'],
                  q.get('is_realtime'), q.get('quote_class')])
            saved += 1
        except Exception as e:
            print(f"  Error saving {q['ticker']} from {q['source']}: {e}")

    print(f"Saved {saved} new quotes")

    # Show summary by source
    print()
    print("Summary by source:")
    summary = con.execute("""
        SELECT source, COUNT(*) as count,
               COUNT(DISTINCT ticker) as tickers,
               AVG(CASE WHEN mid IS NOT NULL THEN 1 ELSE 0 END) * 100 as pct_with_mid
        FROM extended_hours_quotes
        WHERE timestamp_utc >= ?
        GROUP BY source
        ORDER BY count DESC
    """, [now_utc - dt.timedelta(minutes=5)]).fetchall()

    for source, count, ticker_count, pct_mid in summary:
        print(f"  {source:<15}: {count:>3} quotes, {ticker_count:>2} tickers, {pct_mid:>5.1f}% with bid/ask")

    con.close()


if __name__ == "__main__":
    main()
