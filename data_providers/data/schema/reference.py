import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""db_init_reference.py — the structural layer: what things ARE (reference.duckdb).

Three kinds of data live in this project, and they need different homes:

  RECORD (trades.duckdb)      what WE DID          small · irreplaceable · written all session
  SERIES (market_history)     what the market DID  huge · append-only · re-fetchable
  STRUCTURE (reference, here) what things ARE      small · slow-changing · read by EVERYTHING

Structure is small but it is a key, not a note: a screener cannot pick a strike without the
contract's multiplier and expiry; a margin line cannot be computed without tick and margin
rate; a window cannot be cut without session times. It is therefore read by every script,
including during live sessions — so it must never sit in a file a bulk job can lock. Its
own tiny file, written only by small updater scripts, guarantees that.

Populated by scripts/build_reference.py; specs come from TWS reqContractDetails (US),
akshare contract info + 结算单 margins (CN), and what our own data already proves (expiries,
calendars observed from bars). Idempotent: safe to re-run.
"""
import pathlib

import duckdb

ROOT = bootstrap.ROOT
DB = ROOT / "reference.duckdb"
def main(path=None):
    con = duckdb.connect(str(path or DB))

    con.execute("""
CREATE TABLE IF NOT EXISTS instruments (
  underlying TEXT PRIMARY KEY,          -- canonical key every other table joins to
  name TEXT, asset_class TEXT,          -- equity | etf | index | future | fx
  exchange TEXT, currency TEXT, sector TEXT,
  first_seen DATE, last_seen DATE, active BOOLEAN,
  source TEXT, notes TEXT
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS option_contracts (
  symbol TEXT PRIMARY KEY,              -- OCC local symbol, spaces stripped
  underlying TEXT, expiry DATE, strike DOUBLE, call_put TEXT,
  multiplier INTEGER,                   -- 100 US equity/ETF; 10000 CN index (IO); per-product CN
  exercise_style TEXT,                  -- A american | E european
  settlement TEXT,                      -- physical | cash
  first_seen DATE, last_seen DATE, source TEXT
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS expiries (
  underlying TEXT, expiry DATE,
  kind TEXT,                            -- monthly | weekly | quarterly
  last_trade DATE, source TEXT,
  PRIMARY KEY (underlying, expiry)
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS futures_products (   -- CN 品种: SC 原油, MA 甲醇, ...
  product TEXT PRIMARY KEY, exchange TEXT, name TEXT,
  multiplier DOUBLE,                    -- units per lot (SC 1000 桶, MA 10 吨, ...)
  tick DOUBLE,                          -- minimum price move
  margin_rate DOUBLE,                   -- exchange rate; broker adds its own
  currency TEXT, session_note TEXT,     -- day + night session windows
  source TEXT, updated DATE
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS futures_contracts (  -- individual delivery months actually traded
  symbol TEXT PRIMARY KEY,              -- e.g. SC2612, MA701
  product TEXT, exchange TEXT, delivery TEXT,
  expiry DATE, multiplier DOUBLE, tick DOUBLE, margin_rate DOUBLE,
  observed_margin DOUBLE,               -- margin the broker actually took (from 结算单/fills)
  source TEXT, updated DATE
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS calendar (   -- trading days / sessions per venue
  venue TEXT, day DATE, is_open BOOLEAN, session TEXT,
  observed BOOLEAN,                     -- TRUE when proven by bars we hold, FALSE when from a calendar feed
  note TEXT,
  PRIMARY KEY (venue, day, session)
);""")

    # Dividends and splits are NOT copied here: the warehouse already owns them for the US
    # universe (yh.dividends 306k rows, yh.splits 7k) and a second copy would violate the
    # no-duplicate rule. Read them as yh.dividends / yh.splits.

    con.execute("""
CREATE TABLE IF NOT EXISTS earnings (   -- per underlying: when it reports
  underlying TEXT, day DATE,
  timing TEXT,                          -- BMO | AMC | NULL when the vendor does not say
  eps_estimate DOUBLE, eps_actual DOUBLE,
  source TEXT, fetched DATE,
  PRIMARY KEY (underlying, day)
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS equity_events (   -- one row per thing that happened TO a name
  underlying TEXT, day DATE,
  kind TEXT,                             -- earnings | dividend | split | index_add | index_drop
  detail TEXT, source TEXT,
  PRIMARY KEY (underlying, day, kind, detail)
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS events (     -- the calendar that is not price data
  day DATE,
  kind TEXT,                            -- fomc | macro | election | holiday | expiry | opex
  name TEXT, scope TEXT,                -- US | CN | global
  importance TEXT,                      -- high | medium | low
  detail TEXT, source TEXT, fetched DATE,
  PRIMARY KEY (day, kind, name)
);""")

    con.close()
    print("reference schema ready:", DB)


# Guard added by the data/application split: unguarded, importing this module ran it.
if __name__ == "__main__":
    main()
