import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""download_minute_bars.py — 1-minute history into market_history.duckdb.

Two jobs, in priority order:

  --mode fills    every US fill since 2024-02 gets its underlying1-minute session;
                  option fills also get the contract's full session. Date-only exports
                  retain their full tape without treating a placeholder time as execution.
  --mode under    every tier-1 underlying gets 1-minute bars (Alpaca, deep history) over
                  the requested window — realized vol, event reactions, entry/exit timing.

Idempotency: a (symbol, day) is skipped when option_minutes / underlying_minutes already
hold rows for it; inserts are ON CONFLICT DO NOTHING against the primary keys. Re-running
never duplicates and never re-downloads.

    uv run python apps/data_ops/download_minute_bars.py --mode fills
    uv run python apps/data_ops/download_minute_bars.py --mode under \
        --tickers-file plans/data_universe_tier1.txt --start 2024-01-01
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from data_providers.data import clock  # noqa: E402
from data_providers.data import db  # noqa: E402
from data_providers.jobs.data_ops.fill_underlying_minutes import mode_fill_underlyings
from data_providers.shared.net import status  # noqa: E402

ROOT = bootstrap.ROOT
from data_providers.credentials import AlpacaHeaders
HDR = AlpacaHeaders()


def get(url):
    from data_providers.providers.alpaca_http import get_json
    return get_json(url, HDR, tries=4, base=1.5)


def fetch_bars(url_base, symbol, day, key=None):
    """One session of 1-min bars, following next_page_token."""
    out, token = [], None
    while True:
        url = f"{url_base}?symbols={symbol}&timeframe=1Min&start={day}T00:00:00Z&end={day}T23:59:59Z&limit=10000"
        if token:
            url += f"&page_token={token}"
        r = get(url)
        bars = r.get("bars", {})
        out += bars.get(symbol, []) if isinstance(bars, dict) else bars
        token = r.get("next_page_token")
        if not token:
            return out


def occ_of(underlying, expiry, cp, strike):
    """Build the OCC symbol from the structured columns.

    Brokers spell option symbols differently — IBKR pads the root ('META  261120P00630000'),
    Schwab uses 'MU 07/31/2026 755.00 P' — so the broker's string is never trusted as an
    OCC symbol; the parsed columns are the source of truth.
    """
    return f"{str(underlying).strip().upper()}{expiry.strftime('%y%m%d')}{cp}" \
           f"{int(round(float(strike) * 1000)):08d}"


def mode_fills(con):
    rows = con.execute("""
        SELECT DISTINCT underlying, expiry, strike, call_put, CAST(ts_utc AS DATE) AS day
        FROM tr.fills
        WHERE call_put IS NOT NULL AND ts_utc >= DATE '2024-02-01'
          AND broker IN ('IBKR','Schwab') AND underlying IS NOT NULL
          AND expiry IS NOT NULL AND strike IS NOT NULL
        ORDER BY day""").fetchall()
    # (occ, underlying, expiry, strike, call_put, day) — occ built, never taken from the broker
    fills = [(occ_of(u, e, cp, k), u, e, k, cp, d) for u, e, k, cp, d in rows]
    done = {(s, d) for s, d in con.execute(
        "SELECT symbol, CAST(ts_utc AS DATE) FROM mh.option_minutes WHERE source='alpaca_1min' UNION "
        "SELECT symbol, CAST(ts_utc AS DATE) FROM mhro.option_minutes WHERE source='alpaca_1min'").fetchall()}
    todo = [f for f in fills if (f[0], f[5]) not in done]
    print(f"option-fill sessions: {len(fills)} total, {len(todo)} to fetch", flush=True)
    n = failures = 0
    for occ, und, exp, strike, cp, day in todo:
        try:
            bars = fetch_bars("https://data.alpaca.markets/v1beta1/options/bars",
                              occ, day, key=occ)
        except Exception as e:
            failures += 1; print(f"  {occ} {day}: {repr(e)[:70]}", flush=True); time.sleep(1); continue
        # `executemany` REJECTS an empty list (InvalidInputException on DuckDB), so a
        # contract with no bars that session -- halted, half-day, suspended -- threw out
        # of mode_fills and killed the run for every remaining fill in the queue. The
        # try above covers only fetch_bars, not this call. mode_under already guards for
        # exactly this reason ("holidays return zero bars"); the fills path did not.
        if bars:
            con.executemany("""INSERT INTO mh.option_minutes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                                ON CONFLICT DO NOTHING""",
                            [["alpaca_1min", occ, und, exp, strike, cp,
                              b["t"].replace("T", " ")[:19], b["o"], b["h"], b["l"], b["c"],
                              b["v"], b.get("vw")] for b in bars])
        n += len(bars)
        print(f"  {occ} {day}: {len(bars)} bars", flush=True)
        status("download_minute_bars_fills", symbol=occ, day=str(day), bars_total=n)
    print(f"option minutes: +{n} bars; failed sessions={failures}")
    return failures


def mode_under(con, tickers, start, end, chunk_days=10, workers=6):
    """10 days per request, not one.

    Alpaca returns up to 10,000 bars per call and a session is ~960 one-minute bars, so the
    original day-at-a-time loop used ~10% of every request's payload — the reason this job
    crawled (found 2026-10-01 by watching it). A chunk is skipped only when ALL its weekdays
    are already stored; a partial chunk is re-fetched and ON CONFLICT drops what we hold, so
    nothing is duplicated. Fewer, fuller requests also mean the throttle binds less often.
    """
    total = failures = 0
    for tk in tickers:
        days_done = {d for (d,) in con.execute(
            "SELECT DISTINCT CAST(ts_utc AS DATE) FROM mhro.underlying_minutes "
            "WHERE symbol=? AND source='alpaca_1min'", [tk]).fetchall()}
        # build the work list first, then fetch it CONCURRENTLY: a sequential loop cannot fill
        # Alpaca's 200/min because each chunk takes ~1s to transfer (0.7 req/s against a 3.3/s
        # allowance). Workers touch only the network; every write stays on this thread, because
        # DuckDB allows one writer and it is this connection.
        todo, day = [], start
        while day <= end:
            chunk_end = min(day + dt.timedelta(days=chunk_days - 1), end)
            weekdays = [d for d in (day + dt.timedelta(days=i)
                                    for i in range((chunk_end - day).days + 1))
                        if d.weekday() < 5]
            if weekdays and not all(d in days_done for d in weekdays):
                todo.append((day, chunk_end))
            day = chunk_end + dt.timedelta(days=1)
        n = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(fetch_range, tk, a, b): (a, b) for a, b in todo}
            for fut in as_completed(futures):
                a, b = futures[fut]
                try:
                    bars = fut.result()
                except Exception as e:
                    failures += 1; print(f"  {tk} {a}..{b}: {repr(e)[:60]}", flush=True); continue
                if bars:       # holidays return zero bars; executemany rejects an empty list
                    con.executemany("""INSERT INTO mh.underlying_minutes
                                        VALUES (?,?,?,?,?,?,?,NULL,'alpaca_1min')
                                        ON CONFLICT DO NOTHING""",
                                     [[x["t"].replace("T", " ")[:19], tk, x["o"], x["h"],
                                       x["l"], x["c"], x["v"]] for x in bars])
                    n += len(bars)
                    print(f"  {tk} {a}..{b}: {len(bars)} bars (total {n:,})", flush=True)
        total += n
        print(f"{tk}: +{n:,} 1-min bars over {start}→{end} (10-day chunks)", flush=True)
        status("download_minute_bars_under", ticker=tk, bars_total=n)
    print(f"underlying 1-min total this run: {total:,}; failed ranges={failures}")
    return failures


def fetch_range(symbol, start, end):
    """All 1-minute bars in [start, end], following pagination (10,000 bars per page).

    The shared transport paces every HTTP attempt and page across all workers.
    Previously this sweep issued roughly 360 requests/min against a key shared with
    every other Alpaca job and capped at 200. SELFCHECK item S5 exists precisely for this:
    one unthrottled loop competes with its own siblings for the same budget, and the cost is
    429s, wasted retries and dropped batches rather than a clean failure.
    """
    out, token = [], None
    while True:
        url = (f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe=1Min"
               f"&start={start}T00:00:00Z&end={end}T23:59:59Z&limit=10000")
        if token:
            url += f"&page_token={token}"
        r = get(url)
        bars = r.get("bars", {})
        out += bars.get(symbol, []) if isinstance(bars, dict) else bars
        token = r.get("next_page_token")
        if not token:
            return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["fills", "under"], required=True)
    ap.add_argument("--tickers-file", default=str(ROOT / "plans" / "data_universe_tier1.txt"))
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=clock.today().isoformat())
    args = ap.parse_args()

    con = db.connect(log=None, trades_ro=True, staging="alpaca", snapshot=True,
                     snapshot_tables=("option_minutes", "underlying_minutes"),
                     snapshot_columns={t: ("source", "symbol", "ts_utc")
                                       for t in ("option_minutes", "underlying_minutes")})
    if args.mode == "fills":
        errors = mode_fills(con)
        errors += mode_fill_underlyings(con, fetch_range, status)
    else:
        try:
            from data_providers.shared import files
            tickers = files.safe_read_ticker_file(args.tickers_file)
        except (FileNotFoundError, ValueError, OSError) as e:
            print(f"Error: {e}")
            return
        errors = mode_under(con, tickers, dt.date.fromisoformat(args.start),
                   dt.date.fromisoformat(args.end))
    con.close()
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
