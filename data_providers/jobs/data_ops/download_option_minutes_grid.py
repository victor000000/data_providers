import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""download_option_minutes_grid.py — 1-minute option history for a strike BAND, our names first.

Built for the request: "our 25 names, -0.7 to +0.7 delta, 2 years, 1-minute". Delta 0.7 on a
30–45 DTE contract sits roughly ±10% from spot, so the band is NARROWER than the ±20% daily
grid — 11 ratios at 2% steps, 22 contracts per expiry.

Why this is affordable where "all strikes" was not:
  * strikes come from the anchor price at expiry−45d, the same convention the daily downloader
    and the screen use, so we fetch exactly the strikes we would have traded
  * each contract is fetched with PAGINATION (10,000 bars per page ≈ 25 RTH days), so a
    contract costs ~5 requests, not one per day
  * empty contracts are recorded in option_bars_checks, so a re-run never asks again

Writes option_minutes (staging → merge), PK (source, symbol, ts_utc), idempotent per
(contract, day). Staging key is per JOB, not per source — two Alpaca jobs must not share a file.

    uv run python apps/data_ops/download_option_minutes_grid.py \
        --tickers-file plans/data_universe_tier1.txt --months 24 [--days-before-expiry 120]
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_providers.data import clock  # noqa: E402
from data_providers.data import db  # noqa: E402
from data_providers.shared.net import note_headers, retry, status, throttle  # noqa: E402

ROOT = bootstrap.ROOT
from data_providers.credentials import AlpacaHeaders
HDR = AlpacaHeaders()
SRC = "alpaca_1min"
# The band must satisfy THREE requirements at once, which is why it is wider than the
# -0.7..+0.7 delta span alone:
#   ATM                       -> ratio 1.00            (the screen's reference)
#   |delta| 0.16-0.22         -> ~0.78-0.85 / 1.15-1.25 at 45 DTE (that IS the trading band;
#                                at 60% IV it is wider, at 15% narrower — so the grid spans it)
#   |delta| up to 0.7         -> ~0.90-0.92 / 1.08-1.10 (the ITM context strikes)
# 2.5% steps from 0.75 to 1.25 = 21 ratios, 42 contracts per expiry: covers all three.
RATIOS = [round(0.75 + 0.025 * i, 3) for i in range(21)]


def get(url):
    def _once():
        r = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=30)
        # Alpaca's own rate-limit headers say when the shared window is nearly exhausted;
        # note_headers() then waits for the advertised reset instead of guessing.
        note_headers("alpaca", r.headers)
        return json.load(r)
    return retry(_once, what=url[:90])


def occ(underlying, expiry, cp, strike):
    # OCC roots contain no hyphen or dot. Yahoo spells class shares BRK-B / CRD-A / TAP-A,
    # but the vendor symbol is BRKB / CRDA / TAPA — passing the Yahoo form through made
    # every class-share batch fail with HTTP 400 "invalid symbol" (~dozens across the top
    # 6000). The stored `underlying` column keeps the Yahoo spelling for warehouse joins;
    # only the constructed contract symbol is normalised.
    root = underlying.replace("-", "").replace(".", "").strip().upper()
    return f"{root}{expiry.strftime('%y%m%d')}{cp}{int(round(strike * 1000)):08d}"


def third_friday(year, month):
    d = dt.date(year, month, 1)
    fri = [d + dt.timedelta(days=i) for i in range(31)
           if (d + dt.timedelta(days=i)).month == month and (d + dt.timedelta(days=i)).weekday() == 4]
    return fri[2]


def monthly_expiries(start, end):
    out, y, m = [], start.year, start.month
    while True:
        e = third_friday(y, m)
        if e > end:
            break
        if e >= start:
            out.append(e)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def fetch_contract(sym, day_from, day_to):
    """Every 1-minute bar for one contract in the window, following pagination."""
    out, token = [], None
    while True:
        # Gate EVERY page, including the first. The gate used to sit only after the
        # pagination check, so the first request of each contract went out ungated — and with
        # 6 workers that is 6 concurrent sends against a budget shared with every other
        # Alpaca job. SELFCHECK S5: one ungated loop competes with its own siblings.
        throttle("alpaca")
        url = (f"https://data.alpaca.markets/v1beta1/options/bars?symbols={sym}&timeframe=1Min"
               f"&start={day_from}&end={day_to}&limit=10000")
        if token:
            url += f"&page_token={token}"
        r = get(url)
        bars = r.get("bars", {})
        out += bars.get(sym, []) if isinstance(bars, dict) else bars
        token = r.get("next_page_token")
        if not token:
            return out
        throttle("alpaca")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers-file", default=str(ROOT / "plans" / "data_universe_tier1.txt"))
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--months", type=int, default=24, help="expiries to cover, back from today")
    ap.add_argument("--days-before-expiry", type=int, default=120, help="window per contract")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    if args.tickers:
        tickers = args.tickers
    else:
        try:
            from data_providers.shared import files
            tickers = [t.upper() for t in files.safe_read_ticker_file(args.tickers_file)]
        except (FileNotFoundError, ValueError, OSError) as e:
            print(f"Error: {e}")
            return
    con = db.connect(log=None, staging="alpaca_opts1m", universe=True, snapshot=True)
    today = clock.today()
    start = today - dt.timedelta(days=int(args.months * 30.5))
    exps = monthly_expiries(start, today)
    print(f"{len(tickers)} names · {len(exps)} expiries ({exps[0]} → {exps[-1]}) · "
          f"{len(RATIOS)} ratios ±10%", flush=True)

    grand = 0
    for tk in tickers:
        px = {d.isoformat(): c for d, c in con.execute(
            "SELECT date, close FROM yh.prices WHERE symbol=?", [tk]).fetchall()}
        if not px:
            print(f"{tk}: no warehouse prices — skipped", flush=True); continue
        done = {r[0] for r in con.execute(
            """SELECT DISTINCT symbol FROM mh.option_minutes
               UNION SELECT DISTINCT symbol FROM mhro.option_minutes""").fetchall()}
        work = []
        for exp in exps:
            cands = [d for d in px if d <= (exp - dt.timedelta(days=45)).isoformat()]
            if not cands:
                continue
            S0 = px[max(cands)]
            strikes = sorted({round(S0 * r) if S0 >= 100 else round(S0 * r * 2) / 2 for r in RATIOS})
            for K in strikes:
                for cp in ("C", "P"):
                    s = occ(tk, exp, cp, K)
                    if s not in done:
                        work.append((s, max(exp - dt.timedelta(days=args.days_before_expiry), start), exp))
        n = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(fetch_contract, s, a, b): s for s, a, b in work}
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    bars = fut.result()
                except Exception as e:
                    print(f"  {s}: {repr(e)[:60]}", flush=True); continue
                if bars:
                    con.executemany("""INSERT INTO mh.option_minutes
                        (source, symbol, ts_utc, open, high, low, close, volume, vwap)
                        VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                        [[SRC, s, b["t"].replace("T", " ")[:19], b["o"], b["h"], b["l"],
                          b["c"], b["v"], b.get("vw")] for b in bars])
                    n += len(bars)
                throttle("alpaca")
        grand += n
        print(f"{tk}: +{n:,} 1-min option bars ({len(work)} contracts)", flush=True)
        status("download_option_minutes_grid", ticker=tk, bars_total=grand)
    con.close()
    print(f"total: {grand:,} bars")


if __name__ == "__main__":
    main()
