import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""cn_trade_window.py — capture the +-20min trade window around China fills.

For a given day (default: today CST) this stores into trades.duckdb:
  1. underlying_minutes — 1-min bars for SC2612 / MA2701 covering [fill-20min, fill+20min]
     (Sina via akshare futures_zh_minute_sina; CZCE/INE day+night sessions).
  2. qsqh_orders chain snapshot — the option order ladder from a 快期 OrderReport.csv
     export (every cancelled/filled order = a real intraday option price point for our legs).
  3. underlying_daily — EOD daily bars (via fetch_cn_options.py; run that after the close).

Usage:
  uv run python cn_trade_window.py \
      [--date 2026-09-30] [--report <OrderReport.csv>] [--symbols SC2612 MA2701] [--span-min 20]

Sina daily/minute symbols are contract codes (SC2612, MA2701); underlying_ticks uses the
feed codes (nf_SC2612, nf_MA2701). Expiry per contract: SC -> 2026-11-12, MA -> 2026-12-11.
"""
import argparse
import csv
import datetime as dt
import pathlib
import re
import sys
import time

import akshare as ak
import duckdb

DB = bootstrap.ROOT / "trades.duckdb"
CN_ACCOUNT = "20101589"
EXPIRY = {"SC2612": dt.date(2026, 11, 12), "MA701": dt.date(2026, 12, 11),
          "SC": dt.date(2026, 11, 12), "MA": dt.date(2026, 12, 11)}
CST = dt.timezone(dt.timedelta(hours=8))


def minute_bars(symbol, tries=3):
    """The Sina minute endpoint intermittently dies mid-TLS (SSLEOFError) — cost the whole
    2026-10-15 window on the first run, succeeded on the immediate retry. A flake here
    loses the day: the feed is a rolling window, so there is no later backfill."""
    for i in range(tries):
        try:
            df = ak.futures_zh_minute_sina(symbol=symbol, period="1")
            df["dt"] = df["datetime"].astype(str)
            return df
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def store_minutes(con, symbol, day_cst, t0, t1, full_day=False):
    from data_providers.jobs.trades.cn_minute_store import store_minute_frame
    return store_minute_frame(con, symbol, day_cst, t0, t1, minute_bars(symbol), full_day)


def nearest_minute_spot(con, symbol, ts_utc):
    r = con.execute("""SELECT close FROM underlying_minutes WHERE symbol=? AND ts_utc<=?
        ORDER BY ts_utc DESC LIMIT 1""", [symbol, ts_utc]).fetchone()
    if r:
        return r[0]
    r = con.execute("""SELECT close FROM underlying_minutes WHERE symbol=?
        ORDER BY ts_utc ASC LIMIT 1""", [symbol]).fetchone()
    return r[0] if r else None


def store_ladder(con, report_path, day_cst):
    raw = open(report_path, "rb").read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.DictReader(text.splitlines()))
    orders = []
    for r in rows:
        m = re.match(r"^([A-Za-z]+)(\d{3,4})([CP])(\d+(?:\.\d+)?)$", (r.get("合约编码") or "").strip())
        if not m:
            continue
        try:
            px = float(r["价格"])
        except (TypeError, ValueError):
            continue
        orders.append((r["日期"], r["时间"], m.group(1).upper() + m.group(2), m.group(3),
                       float(m.group(4)), px, r["状态"].strip(), r["开平"].strip()))
    if not orders:
        print("  ladder: no parsable orders")
        return None
    # CSV is newest-first; snapshot ts = the ladder START (earliest order)
    first_ts = min(dt.datetime.strptime(f"{o[0]} {o[1]}", "%Y/%m/%d %H:%M:%S") for o in orders)
    ts_utc = first_ts - dt.timedelta(hours=8)
    # idempotent: drop prior ladder snapshot(s) from the same day
    con.execute("""DELETE FROM chain_rows WHERE snap_id IN (SELECT snap_id FROM chain_snapshots
        WHERE source='qsqh_orders' AND CAST(ts_utc AS DATE)=CAST(? AS DATE))""", [ts_utc])
    con.execute("""DELETE FROM chain_snapshots WHERE source='qsqh_orders'
        AND CAST(ts_utc AS DATE)=CAST(? AS DATE)""", [ts_utc])
    snap = con.execute("""INSERT INTO chain_snapshots (ts_utc, underlying, expiry, spot, source)
        VALUES (?,?,?,?, 'qsqh_orders') RETURNING snap_id""",
        [ts_utc, orders[0][2], EXPIRY.get(orders[0][2]), None]).fetchone()[0]
    for _, t, und, cp, K, px, status, oc in orders:
        ots = dt.datetime.strptime(f"{day_cst} {t}", "%Y-%m-%d %H:%M:%S") - dt.timedelta(hours=8)
        spot = nearest_minute_spot(con, und, ots)
        con.execute("INSERT INTO chain_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [snap, und, EXPIRY.get(und), K, cp, None, None, None, None,
                     px, None, None, None, None, None, None, None, spot])
    con.execute("UPDATE chain_snapshots SET spot=? WHERE snap_id=?",
                [nearest_minute_spot(con, orders[0][2], ts_utc), snap])
    print(f"  ladder: {len(orders)} order price points -> snapshot #{snap} ({orders[0][2]})")
    return snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD, default today CST")
    ap.add_argument("--report", help="快期 OrderReport.csv for the day")
    ap.add_argument("--symbols", nargs="*", default=["SC2612", "MA2701"])
    ap.add_argument("--span-min", type=int, default=20)
    ap.add_argument("--full-day", action="store_true",
                    help="store the whole trading day (prev night session 21:00 -> 15:00) instead of the fill window")
    args = ap.parse_args()

    day = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(CST).date()
    con = duckdb.connect(str(DB))

    fills = con.execute("""SELECT DISTINCT symbol, ts_utc FROM fills
        WHERE account=? AND CAST(ts_utc AS DATE)=CAST(? AS DATE)""", [CN_ACCOUNT, day.isoformat()]).fetchall()
    if fills:
        f0 = min(f[1] for f in fills)
        f1 = max(f[1] for f in fills)
    else:
        f0, f1 = dt.datetime(day.year, day.month, day.day, 1, 0), dt.datetime(day.year, day.month, day.day, 1, 30)

    t0 = (dt.datetime.combine(day, dt.time(9, 0)) - dt.timedelta(minutes=args.span_min))
    t0 = max(t0, (f0.replace(tzinfo=None) + dt.timedelta(hours=8)) - dt.timedelta(minutes=args.span_min))
    t1 = min((f1.replace(tzinfo=None) + dt.timedelta(hours=8)) + dt.timedelta(minutes=args.span_min),
             dt.datetime.combine(day, dt.time(15, 0)))
    print(f"window (CST): {t0} .. {t1}")
    for sym in args.symbols:
        n = store_minutes(con, sym, day, t0.time(), t1.time(), full_day=args.full_day)
        print(f"  {sym}: {n} minute bars stored")
    if args.report:
        store_ladder(con, args.report, day.isoformat())
    con.close()


if __name__ == "__main__":
    main()
