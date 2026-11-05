#!/usr/bin/env python3
"""Measure how much money actually trades in each US-listed ETF.

Split out of 15_fetch_etf_liquidity.py so the measurement is testable without running
the ten-minute sweep.

LIQUIDITY IS DOLLAR VOLUME -- close x volume -- as the MEDIAN over the last month.
Median and not mean because one expiry or rebalance day makes the mean meaningless for
exactly the funds whose turnover is spiky; dollars and not share count because 10,000
shares of a $400 fund is not the same trade as 10,000 of a $4 one.
"""
import json
import os
import sys
import time

from data_providers.warehouse import config as C  # noqa: E402

BATCH = 250
LOOKBACK = "1mo"      # a month of sessions: enough for a median, short enough to be current
MIN_BARS = 10         # below this a fund is too new to have a liquidity worth ranking
SCREENER = ("https://api.nasdaq.com/api/screener/etf"
            "?tableonly=true&limit=5000&offset=0&download=true")
CACHE = os.path.join(C.SOURCES, "independent", "etf_liquidity_cache.json")


def screener():
    """Every ETF the US market lists, as {symbol: name}."""
    import requests
    r = requests.get(SCREENER, timeout=120, headers={
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    r.raise_for_status()
    rows = r.json()["data"]["data"]["rows"]
    return {x["symbol"]: x["companyName"] for x in rows if x.get("symbol")}


def median_dollar_volume(symbols):
    """{symbol: median daily $volume}; symbols without usable bars are left out."""
    import pandas as pd
    import yfinance as yf
    df = yf.download(list(symbols), period=LOOKBACK, interval="1d", auto_adjust=False,
                     progress=False, group_by="ticker", threads=True, timeout=120)
    out = {}
    for s in symbols:
        try:
            one = df[s].dropna(subset=["Close", "Volume"])
        except Exception:
            continue
        # MIN_BARS is the floor: three days of noise would rank a new fund above a
        # mature one, and its dollar volume would say nothing about its liquidity.
        if len(one) < MIN_BARS:
            continue
        dv = (one["Close"] * one["Volume"]).median()
        if pd.notna(dv) and dv > 0:
            out[s] = float(dv)
    return out


def cached_bars(todo):
    """Fill `todo` from the cache, fetching only what is missing."""
    got = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    todo = [s for s in todo if s not in got]
    for i in range(0, len(todo), BATCH):
        part = todo[i:i + BATCH]
        t0 = time.time()
        got.update(median_dollar_volume(part))
        # A symbol the fetch could not price is cached as None, not left absent --
        # otherwise every re-run retries the same delisted tickers forever.
        for s in part:
            got.setdefault(s, None)
        json.dump(got, open(CACHE, "w"))
        print(f"  {i + len(part):>5}/{len(todo)} in {time.time() - t0:.0f}s "
              f"({sum(1 for v in got.values() if v)} priced)", flush=True)
    return got




def cached(symbols):
    """Fill `symbols` from the cache, fetching only what is missing.

    The cache is what makes a ten-minute sweep acceptable to repeat: an interrupted
    run resumes where it stopped.
    """
    got = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    todo = [s for s in symbols if s not in got]
    for i in range(0, len(todo), BATCH):
        part = todo[i:i + BATCH]
        t0 = time.time()
        got.update(median_dollar_volume(part))
        # A symbol the fetch could not price is cached as None rather than left
        # absent -- otherwise every re-run retries the same delisted tickers forever.
        for s in part:
            got.setdefault(s, None)
        json.dump(got, open(CACHE, "w"))
        print(f"  {i + len(part):>5}/{len(todo)} in {time.time() - t0:.0f}s "
              f"({sum(1 for v in got.values() if v)} priced)", flush=True)
    return got
