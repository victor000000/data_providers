import os
import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""build_calendar_events.py — the calendar layer: sessions, earnings, macro events.

Sources, in role order, each verified live 2026-09-30 (see plans/DATABASE_DESIGN.md §8):

  trading days   Futu  request_trading_days(market=TradeDateMarket.{US,CN,HK})   primary, free plan
                 Alpaca GET /v2/calendar                                        US cross-check (early closes)
                 akshare tool_trade_date_hist_sina                              CN fallback
  earnings       Futu  get_earnings_calendar   (7-day windows; US/SH/HK)         primary — BMO/AMC + EPS est
                 yfinance get_earnings_dates                                    cross-check for our tickers
  economic       Futu  get_economic_calendar   (title/country/star/prev/cons/act) primary
                 akshare news_economic_baidu                                    fallback
  rate path      Futu  get_fed_watch_target_rate()                               meeting + probability
  FOMC dates     federalreserve.gov calendar                                     the Fed's own publication

Dividends and splits are NOT stored here: the warehouse owns them (yh.dividends 306k,
yh.splits 7k). Alpaca's corporate-actions endpoint covers splits/spin-offs/mergers but
not cash dividends, so it adds nothing the warehouse lacks.

Cross-checks land in trades.source_checks (AGREE/DIFFER, with counts) — the same
discipline as crosscheck_sources.py.

    uv run --no-project --with duckdb --with futu-api --with yfinance \
        python apps/data_ops/build_calendar_events.py [--tickers-file ...] [--days-forward 60]
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.request

import pandas as pd

from data_providers.shared.net import throttle

from data_providers.data import clock  # noqa: E402
from data_providers.data import db  # noqa: E402

ROOT = bootstrap.ROOT
TODAY = clock.today()
FOMC_PUBLISHED = [  # federalreserve.gov/monetarypolicy/fomccalendars.htm, fetched 2026-09-30
    ("2026-01-28", "Jan 27-28"), ("2026-03-18", "Mar 17-18 · projections"),
    ("2026-04-29", "Apr 28-29"), ("2026-06-17", "Jun 16-17 · projections"),
    ("2026-07-29", "Jul 28-29"), ("2026-09-16", "Sep 15-16 · projections"),
    ("2026-10-28", "Oct 27-28"), ("2026-12-09", "Dec 8-9 · projections"),
]
STAR = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


def futu_connect():
    from futu import OpenQuoteContext
    return OpenQuoteContext(host=os.environ.get("FUTU_HOST", "host.docker.internal" if pathlib.Path("/.dockerenv").exists() else "127.0.0.1"), port=11111)


def trading_days(ctx, con, market_enum, venue, start, end):
    import futu
    ret, data = ctx.request_trading_days(market=market_enum, start=start.isoformat(),
                                         end=end.isoformat())
    if ret != 0:
        print(f"  {venue}: Futu trading days failed — {str(data)[:80]}")
        return set()
    rows, open_days = [], set()
    for r in data:
        day = dt.date.fromisoformat(r["time"])
        half = r.get("trade_date_type") == "HALF"
        rows.append((venue, day, True, "half" if half else "full", False,
                     "Futu request_trading_days" + (" (half day)" if half else "")))
        open_days.add(day)
    # A day is only written as CLOSED where the feed is actually authoritative: the
    # exchange calendar is published ~1 year ahead, and marking unpublished future
    # weekdays as holidays would be an assertion the source never made.
    horizon = max(open_days) if open_days else start
    d = start
    while d <= min(end, horizon):
        if d.weekday() < 5 and d not in open_days:
            rows.append((venue, d, False, "closed", False,
                         "not a trading day (Futu, within its published horizon)"))
        d += dt.timedelta(days=1)
    if rows:   # DuckDB raises on an empty list, not a no-op
        con.executemany("INSERT OR REPLACE INTO ref.calendar VALUES (?,?,?,?,?,?)", rows)
    print(f"  {venue}: {len(open_days)} trading days, "
          f"{sum(1 for r in rows if not r[2])} holidays, horizon {horizon}")
    return open_days, horizon


def alpaca_calendar(con, start, end):
    """US cross-check: Alpaca publishes open/close per day, including early closes."""
    try:
        sec = json.loads((ROOT / "secrets" / "alpaca.json").read_text())
        req = urllib.request.Request(
            f"https://paper-api.alpaca.markets/v2/calendar?start={start}&end={end}",
            headers={"APCA-API-KEY-ID": sec["key_id"], "APCA-API-SECRET-KEY": sec["secret"]})
        days = json.load(urllib.request.urlopen(req, timeout=20))
    except Exception as e:
        return None, f"alpaca calendar unavailable: {repr(e)[:60]}"
    early = [d for d in days if d["close"] != "16:00"]
    return {dt.date.fromisoformat(d["date"]) for d in days}, \
        f"{len(days)} sessions, {len(early)} early closes"


def earnings_futu(ctx, con, market_enum, label, start, end):
    """Futu caps the query at 7 days, so walk the window in 7-day steps."""
    import futu  # noqa: F401
    n, d = 0, start
    while d <= end:
        stop = min(d + dt.timedelta(days=6), end)
        throttle('futu')   # 30 req/30s per category; the 7-day window walk is ~22 calls
        ret, data = ctx.get_earnings_calendar(market_enum, begin_date=d.isoformat(),
                                              end_date=stop.isoformat())
        if ret == 0 and isinstance(data, pd.DataFrame):
            for _, r in data.iterrows():
                sym = str(r.get("security", "")).split(".")[-1]
                day = dt.date.fromisoformat(str(r.get("earnings_date")))
                timing = {"BEFORE": "BMO", "AFTER": "AMC"}.get(str(r.get("pub_type")), None)
                def num(v):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None
                con.execute("INSERT OR REPLACE INTO ref.earnings VALUES (?,?,?,?,?,?,?)",
                            [sym, day, timing, num(r.get("eps_predict")),
                             num(r.get("eps_actual")), f"futu_earnings_{label}", TODAY])
                n += 1
        d = stop + dt.timedelta(days=1)
    print(f"  earnings {label}: {n} rows")
    return n


def earnings_crosscheck(con, tickers):
    """Futu vs yfinance for the tickers we trade — disagreement is the signal."""
    import yfinance as yf
    rows = []
    for tk in tickers:
        futu_days = {r[0] for r in con.execute(
            "SELECT day FROM ref.earnings WHERE underlying=? AND source LIKE 'futu%'",
            [tk]).fetchall()}
        try:
            df = yf.Ticker(tk).get_earnings_dates(limit=8)
            yf_days = {ts.date() for ts in df.index} if df is not None and not df.empty else set()
        except Exception:
            yf_days = set()
        if not futu_days and not yf_days:
            continue
        a, b = futu_days & yf_days, futu_days ^ yf_days
        rows.append((f"E1:{tk}", tk, "futu_earnings", "yfinance", len(a | b),
                     "AGREE" if not b else "DIFFER",
                     f"shared {sorted(a)}; differs {sorted(b)}"))
    return rows


def events_futu(ctx, con, start, end):
    res = ctx.get_economic_calendar(start.isoformat(), end_date=end.isoformat())
    ret, data = res[0], res[1]          # this call returns a 4-tuple, not a 2-tuple
    if ret != 0 or not isinstance(data, pd.DataFrame):
        print(f"  macro events: Futu failed — {str(data)[:80]}")
        return 0
    n = 0
    for _, r in data.iterrows():
        ts = r.get("timestamp")
        day = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).date() if ts else start
        con.execute("INSERT OR REPLACE INTO ref.events VALUES (?,?,?,?,?,?,?,?)",
                    [day, "macro", str(r.get("title"))[:200], str(r.get("country")),
                     STAR.get(str(r.get("star")).upper()),
                     f"prev={r.get('previous')} cons={r.get('consensus')} act={r.get('actual')}",
                     "futu_economic_calendar", TODAY])
        n += 1
    print(f"  macro events: {n} rows")
    return n


def fedwatch(ctx, con):
    ret, data = ctx.get_fed_watch_target_rate()
    if ret != 0 or not isinstance(data, pd.DataFrame):
        print(f"  fedwatch: failed — {str(data)[:60]}")
        return
    for _, r in data.iterrows():
        con.execute("INSERT OR REPLACE INTO ref.events VALUES (?,?,?,?,?,?,?,?)",
                    [str(r.get("meeting_date")), "fedwatch", str(r.get("target_range")),
                     "US", "high", f"probability {r.get('probability')}%",
                     "futu_get_fed_watch_target_rate", TODAY])
    print(f"  fedwatch: {len(data)} probability rows")


def fomc_published(con):
    for day, detail in FOMC_PUBLISHED:
        con.execute("INSERT OR REPLACE INTO ref.events VALUES (?,?,?,?,?,?,?,?)",
                    [day, "fomc", "FOMC decision", "US", "high", detail,
                     "federalreserve.gov/monetarypolicy/fomccalendars.htm", TODAY])
    print(f"  FOMC: {len(FOMC_PUBLISHED)} 2026 meetings (the Fed's own calendar)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers-file", default=str(ROOT / "plans" / "data_universe_tier1.txt"))
    ap.add_argument("--days-forward", type=int, default=60)
    ap.add_argument("--cal-back", type=int, default=400)
    ap.add_argument("--cal-forward", type=int, default=400)
    args = ap.parse_args()
    try:
        from data_providers.shared import files
        tickers = files.safe_read_ticker_file(args.tickers_file)
    except (FileNotFoundError, ValueError, OSError) as e:
        print(f"Error: {e}")
        return

    import futu
    con = db.connect(log="rw", reference="rw", universe=True)
    start, end = TODAY - dt.timedelta(days=args.cal_back), TODAY + dt.timedelta(days=args.cal_forward)
    checks = []

    print("trading calendars (Futu primary):")
    ctx = futu_connect()
    try:
        us, us_horizon = trading_days(ctx, con, futu.TradeDateMarket.US, "US", start, end)
        cn, cn_horizon = trading_days(ctx, con, futu.TradeDateMarket.CN, "CN", start, end)
        alpaca_days, note = alpaca_calendar(con, start.isoformat(), end.isoformat())
        if alpaca_days is not None:
            # compare only inside Futu's published horizon — past it, Futu makes no claim
            diff = {d for d in (us ^ alpaca_days) if d <= us_horizon}
            n = len({d for d in (us | alpaca_days) if d <= us_horizon})
            checks.append(("K1", "US sessions", "futu_trading_days", "alpaca_calendar", n,
                           "AGREE" if not diff else "DIFFER",
                           f"{note}; compared to {us_horizon}; differs {sorted(diff)[:5]}"))
            print(f"  US cross-check vs Alpaca: {note} — compared to {us_horizon}: "
                  f"{'AGREE' if not diff else f'{len(diff)} days differ: {sorted(diff)[:5]}'}")

        print("earnings (Futu, 7-day windows):")
        earnings_futu(ctx, con, futu.Market.US, "us", TODAY - dt.timedelta(days=7),
                      TODAY + dt.timedelta(days=args.days_forward))
        earnings_futu(ctx, con, futu.Market.SH, "cn", TODAY - dt.timedelta(days=7),
                      TODAY + dt.timedelta(days=args.days_forward))
        checks += earnings_crosscheck(con, tickers)

        print("economic events:")
        events_futu(ctx, con, TODAY, TODAY + dt.timedelta(days=args.days_forward))
        fedwatch(ctx, con)
        fomc_published(con)
    finally:
        ctx.close()

    print("\nour tickers' earnings inside the next expiry cycle:")
    for r in con.execute("""
            SELECT underlying, day, timing, eps_estimate FROM ref.earnings
            WHERE list_contains(?, underlying)
              AND day BETWEEN CURRENT_DATE AND CURRENT_DATE + 45
            ORDER BY day""", [tickers]).fetchall():
        print(f"  {r[0]:6} {r[1]} {str(r[2] or ''):4} EPS est {r[3]}")

    if checks:
        con.execute("""CREATE TABLE IF NOT EXISTS source_checks (
            run_ts TIMESTAMP, check_id TEXT, subject TEXT, src_a TEXT, src_b TEXT,
            n INTEGER, verdict TEXT, detail TEXT)""")
        con.executemany("INSERT INTO source_checks VALUES (?,?,?,?,?,?,?,?)",
                        [(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None), *c) for c in checks])
    for t in ("calendar", "earnings", "events"):
        print(f"ref.{t}: {con.execute(f'SELECT count(*) FROM ref.{t}').fetchone()[0]} rows")
    con.close()


if __name__ == "__main__":
    main()
