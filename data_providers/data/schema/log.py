import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""db_init.py — create trades.duckdb schema v2 (idempotent, full field set).

Field coverage: 盘口 (bid/ask+sizes) · underlying px · greeks (delta/gamma/theta/vega)
· OI · volume · premium · strike · symbol · volatility (IV) · fees · fill time
· order type & placement time · margin · realized P&L · account daily stats.
"""
import pathlib
import duckdb

DB = bootstrap.ROOT / "trades.duckdb"
def main(path=None):
    con = duckdb.connect(str(path or DB))

    con.execute("CREATE SEQUENCE IF NOT EXISTS fills_seq START 1;")
    con.execute("""
CREATE TABLE IF NOT EXISTS fills (
  fill_id INTEGER PRIMARY KEY DEFAULT nextval('fills_seq'),
  ts_utc TIMESTAMP, ts_orig TEXT,              -- 成交时间
  order_ts_utc TIMESTAMP, order_type TEXT,     -- 挂单时间 / 挂单方式 (LIMIT/MARKET/COMBO)
  account TEXT, broker TEXT,
  underlying TEXT, symbol TEXT, security_type TEXT,
  expiry DATE, strike DOUBLE, call_put TEXT,
  action TEXT, open_close TEXT,
  qty INTEGER, price DOUBLE,                   -- premium (per share)
  fee DOUBLE, realized_pnl DOUBLE,             -- 手续费 / 平仓盈亏
  exchange TEXT, currency TEXT,
  bid_at_fill DOUBLE, ask_at_fill DOUBLE,      -- 盘口
  bid_size DOUBLE, ask_size DOUBLE,
  oi_at_fill DOUBLE, volume_day_at_fill DOUBLE,-- 持仓量 / 市场成交量
  underlying_px DOUBLE,                        -- Underlying价格
  iv_at_fill DOUBLE, iv_mid DOUBLE,            -- volatility (from fill price / from mid)
  delta_at_fill DOUBLE, gamma_at_fill DOUBLE, theta_at_fill DOUBLE, vega_at_fill DOUBLE,
  margin_local DOUBLE, margin_currency TEXT,   -- 保证金资金占用
  source TEXT, notes TEXT
);""")

    con.execute("CREATE SEQUENCE IF NOT EXISTS snap_seq START 1;")
    con.execute("""
CREATE TABLE IF NOT EXISTS chain_snapshots (
  snap_id INTEGER PRIMARY KEY DEFAULT nextval('snap_seq'),
  ts_utc TIMESTAMP, underlying TEXT, expiry DATE, spot DOUBLE, source TEXT
);""")
    con.execute("""
CREATE TABLE IF NOT EXISTS chain_rows (
  snap_id INTEGER, underlying TEXT, expiry DATE,
  strike DOUBLE, call_put TEXT,
  bid DOUBLE, ask DOUBLE, bid_size DOUBLE, ask_size DOUBLE,
  mid DOUBLE, iv DOUBLE,
  delta DOUBLE, gamma DOUBLE, theta DOUBLE, vega DOUBLE,
  oi DOUBLE, volume DOUBLE, spot DOUBLE
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS account_daily (
  day DATE, account TEXT, currency TEXT,
  net_liq DOUBLE, margin_used DOUBLE, available DOUBLE,
  realized_pnl DOUBLE, unrealized_pnl DOUBLE, fees DOUBLE, risk_ratio DOUBLE,
  source TEXT, notes TEXT
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS underlying_ticks (
  ts_utc TIMESTAMP, symbol TEXT, price DOUBLE, source TEXT, note TEXT
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS underlying_daily (
  day DATE, symbol TEXT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
  settle DOUBLE, volume DOUBLE, oi DOUBLE, source TEXT
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS underlying_minutes (
  ts_utc TIMESTAMP, symbol TEXT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
  volume DOUBLE, oi DOUBLE, source TEXT
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT, account TEXT, broker TEXT, underlying TEXT, symbol TEXT,
  action TEXT, open_close TEXT, order_type TEXT, validity TEXT,
  limit_price DOUBLE, qty DOUBLE, filled_qty DOUBLE, avg_fill_price DOUBLE,
  status TEXT, ts_submit_utc TIMESTAMP, exchange TEXT, currency TEXT,
  notes TEXT, source TEXT
);""")

    con.execute("""
CREATE TABLE IF NOT EXISTS journal_entries (
  row_num INTEGER, ts TEXT, broker TEXT, plan_time TEXT,
  -- major_plan was MISSING, and it is the column the user actually writes in: every
  -- session's stated plan lives there ("care about the CRWD put legs and META put legs,
  -- roll to long DTE Dec 18"). Its absence also shifted the positional map, so
  -- alt_assumptions held major plans and confidence held alternative assumptions -- see
  -- FIELDS in load_journal.py, which now maps by header name.
  major_assumption TEXT, major_plan TEXT, alt_assumptions TEXT, confidence TEXT,
  plan_fixed_time TEXT, order_thinking TEXT,
  raw_json TEXT, source_file TEXT
);""")

    con.execute("""
CREATE OR REPLACE VIEW v_daily_stats AS
SELECT CAST(date_trunc('day', ts_utc) AS DATE) AS day, account,
       count(*) AS n_fills,
       sum(abs(qty)) AS lots,
       round(sum(fee), 2) AS fees,
       round(sum(realized_pnl), 2) AS realized_pnl
FROM fills
WHERE ts_utc IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 2;""")

    con.close()
    print("db ready (v2):", DB)


# Guard added by the data/application split: unguarded, importing this module ran it.
if __name__ == "__main__":
    main()
