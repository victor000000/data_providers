import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""db_init_history.py — schema additions for market_history.duckdb (idempotent).

option_minutes  : 1-minute option bars (Alpaca, 2024-02→). This is the table that makes the
                  trade log reproducible: for every US option fill since Feb 2024 the tape
                  around the fill can be re-read, instead of being lost at trade time.
fundamentals    : per-day snapshots from fundamentals_snapshot (market cap, shares, sector).
index_levels    : VIX and friends — kept in underlying_daily (symbol '^VIX'), no new table.
"""
import pathlib

import duckdb

DDL = [
    """CREATE TABLE IF NOT EXISTS {p}option_minutes (
        source TEXT, symbol TEXT, underlying TEXT, expiry DATE, strike DOUBLE, call_put TEXT,
        ts_utc TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        volume DOUBLE, vwap DOUBLE, PRIMARY KEY (source, symbol, ts_utc))""",
    """CREATE TABLE IF NOT EXISTS {p}option_flow_daily (
        day DATE, underlying TEXT, source TEXT, call_oi DOUBLE, put_oi DOUBLE,
        iv_rank DOUBLE, iv_percentile DOUBLE, hv30 DOUBLE, hv365 DOUBLE,
        call_vol DOUBLE, put_vol DOUBLE, pcr_vol DOUBLE, pcr_oi DOUBLE, gex_proxy DOUBLE,
        method TEXT, note TEXT, PRIMARY KEY (day, underlying, source))""",
    """CREATE TABLE IF NOT EXISTS {p}fundamentals_snapshot (
        day DATE, symbol TEXT, market_cap DOUBLE, shares_outstanding DOUBLE,
        float_shares DOUBLE, sector TEXT, industry TEXT, source TEXT,
        PRIMARY KEY (day, symbol))""",
    """CREATE TABLE IF NOT EXISTS {p}option_bars (
        source TEXT, symbol TEXT, underlying TEXT, expiry DATE, strike DOUBLE, call_put TEXT,
        day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
        vwap DOUBLE, PRIMARY KEY (source, symbol, day))""",
    """CREATE TABLE IF NOT EXISTS {p}option_bars_checks (
        source TEXT, underlying TEXT, expiry DATE, bars INTEGER, checked_utc TIMESTAMP,
        PRIMARY KEY (source, underlying, expiry))""",
    """CREATE TABLE IF NOT EXISTS {p}underlying_minutes (
        ts_utc TIMESTAMP, symbol TEXT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        volume DOUBLE, oi DOUBLE, source TEXT, PRIMARY KEY (ts_utc, symbol, source))""",
    """CREATE TABLE IF NOT EXISTS {p}underlying_daily (
        day DATE, symbol TEXT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        settle DOUBLE, volume DOUBLE, oi DOUBLE, source TEXT,
        PRIMARY KEY (day, symbol, source))""",
    """CREATE TABLE IF NOT EXISTS {p}iv_history (
        day DATE, underlying TEXT, expiry DATE, atm_iv DOUBLE, spot DOUBLE, source TEXT,
        PRIMARY KEY (day, underlying, source))""",
    # Depth, not just freshness. A vendor IV request returns a ROLLING window, so
    # "is the newest row recent?" answers the wrong question: the 24 tier-1 names held a
    # 1-year series, were judged fresh, and would have stayed a year deep forever while the
    # rest of the universe got two. This table remembers how far back each symbol has EVER
    # reached, which is what distinguishes "needs a deeper pull" from "that is all the
    # vendor has" — the second must terminate, or a recent IPO is re-requested forever.
    """CREATE TABLE IF NOT EXISTS {p}iv_history_checks (
        source TEXT, underlying TEXT, oldest_seen DATE, newest_seen DATE, bars INTEGER,
        checked_utc TIMESTAMP, PRIMARY KEY (source, underlying))""",
    """CREATE TABLE IF NOT EXISTS {p}option_greeks_daily (
        source TEXT, symbol TEXT, underlying TEXT, expiry DATE, strike DOUBLE, call_put TEXT,
        day DATE, iv DOUBLE, delta DOUBLE, gamma DOUBLE, theta DOUBLE, vega DOUBLE, rho DOUBLE,
        PRIMARY KEY (source, symbol, day))""",
    """CREATE TABLE IF NOT EXISTS {p}option_oi_daily (
        source TEXT, symbol TEXT, underlying TEXT, expiry DATE, strike DOUBLE, call_put TEXT,
        day DATE, oi DOUBLE, volume DOUBLE,
        PRIMARY KEY (source, symbol, day))""",
]


def ensure(con, prefix="mh."):
    """Create the series tables on an attached database (used by staging and by db.py)."""
    for ddl in DDL:
        con.execute(ddl.format(p=prefix))

def _build():
    """The script entry point: create the tables in the real market_history.duckdb."""
    ROOT = bootstrap.ROOT
    con = duckdb.connect(str(ROOT / "market_history.duckdb"))

    con.execute("""
    CREATE TABLE IF NOT EXISTS option_minutes (
      source TEXT, symbol TEXT, underlying TEXT, expiry DATE, strike DOUBLE, call_put TEXT,
      ts_utc TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
      volume DOUBLE, vwap DOUBLE,
      PRIMARY KEY (source, symbol, ts_utc)
    );""")

    con.execute("""
    CREATE TABLE IF NOT EXISTS option_flow_daily (
      day DATE, underlying TEXT, source TEXT,
      -- inputs we cannot re-derive later: aggregate open interest and vendor panel numbers
      call_oi DOUBLE, put_oi DOUBLE,
      iv_rank DOUBLE, iv_percentile DOUBLE, hv30 DOUBLE, hv365 DOUBLE,
      -- inputs derivable from option_bars but stored once so the screener is a single query
      call_vol DOUBLE, put_vol DOUBLE,
      -- derived ratios / exposure (documented method; rebuildable from the inputs above)
      pcr_vol DOUBLE, pcr_oi DOUBLE, gex_proxy DOUBLE,
      method TEXT, note TEXT,
      PRIMARY KEY (day, underlying, source)
    );""")

    con.execute("""
    CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
      day DATE, symbol TEXT,
      market_cap DOUBLE, shares_outstanding DOUBLE, float_shares DOUBLE,
      sector TEXT, industry TEXT, source TEXT,
      PRIMARY KEY (day, symbol)
    );""")

    con.close()
    print("market_history schema ready (option_minutes, fundamentals_snapshot)")



if __name__ == "__main__":
    _build()
