import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""record_trade_cn.py — record a China futures-option fill into trades.duckdb.

Fetches the underlying price live from Sina (GBK quote feed). IV/greeks: pass the
values shown in your broker platform (快期) via --iv (greeks then computed by BS),
or omit them and they stay NULL until backfilled.

Example:
  uv run python record_trade_cn.py \
    --account 20101589 --exchange 郑商所 --contract MA701P2650 \
    --action SELL --open-close OPEN --qty 19 --price 82.5 --fee 9.79 \
    --time "2026-09-28 10:41:19" --sina nf_MA2701 --expiry 2026-12-11 \
    --iv 0.28 --margin-local 114507.8 --notes "快期挂单"

Sina code mapping: SC2612 -> nf_SC2612 · MA701 -> nf_MA2701 (CZCE uses 4-digit
year-month on Sina). MO (中金所) index options are not on the Sina nf feed.
"""
import argparse
import datetime as dt
import pathlib
import re
import sys
import urllib.request

import duckdb

from data_providers.compute.bs import bs_greeks, T_years

DB = bootstrap.ROOT / "trades.duckdb"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
from data_providers.shared.rates import R_US as R


def sina_price(code):
    url = "https://hq.sinajs.cn/list=" + code
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"})
    raw = urllib.request.urlopen(req, timeout=15).read()
    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    m = re.search(r'="([^"]*)"', text)
    if not m or not m.group(1):
        return None
    f = m.group(1).split(",")
    for idx in (8, 6, 2, 0):  # 最新价 / 买价 / 开盘 / 首字段
        if len(f) > idx:
            try:
                px = float(f[idx])
                if px > 0:
                    return px
            except ValueError:
                continue
    return None


def parse_contract(c):
    m = re.match(r"^([A-Za-z]+\d+)[-\s]?([CP])-?(\d+(?:\.\d+)?)$", c)
    if not m:
        return None, None, None
    return m.group(1), m.group(2), float(m.group(3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="20101589")
    ap.add_argument("--broker", default="浙商期货")
    ap.add_argument("--exchange", default="")
    ap.add_argument("--contract", required=True, help="e.g. MA701P2650 / sc2612C880 / MO2610-P-7400")
    ap.add_argument("--action", required=True, choices=["BUY", "SELL", "买", "卖"])
    ap.add_argument("--open-close", default="")
    ap.add_argument("--qty", type=int, required=True)
    ap.add_argument("--price", type=float, required=True)
    ap.add_argument("--fee", type=float)
    ap.add_argument("--realized-pnl", type=float)
    ap.add_argument("--time", default="")
    ap.add_argument("--sina", help="Sina code for underlying, e.g. nf_MA2701")
    ap.add_argument("--expiry", help="YYYY-MM-DD (for greeks when --iv given)")
    ap.add_argument("--iv", type=float, help="IV from broker platform (e.g. 0.28)")
    ap.add_argument("--margin-local", type=float)
    ap.add_argument("--notes", default="")
    ap.add_argument("--source", default="live_record_cn")
    args = ap.parse_args()

    action = {"买": "BUY", "卖": "SELL"}.get(args.action, args.action)
    und, cp, strike = parse_contract(args.contract)

    px = None
    if args.sina:
        try:
            px = sina_price(args.sina)
        except Exception as e:
            print("sina fetch failed:", e)

    greeks = (None, None, None, None)
    if args.iv and args.expiry and px and strike:
        T = T_years(dt.date.fromisoformat(args.expiry))
        greeks = bs_greeks(px, strike, T, R, args.iv, cp == "C")

    ts = None
    if args.time:
        try:
            ts = dt.datetime.strptime(args.time, "%Y-%m-%d %H:%M:%S") - dt.timedelta(hours=8)
        except ValueError:
            ts = None

    con = duckdb.connect(str(DB))
    con.execute("""INSERT INTO fills (ts_utc, ts_orig, account, broker, underlying, symbol,
        security_type, expiry, strike, call_put, action, open_close, qty, price, fee,
        realized_pnl, exchange, currency, underlying_px, iv_at_fill,
        delta_at_fill, gamma_at_fill, theta_at_fill, vega_at_fill,
        margin_local, margin_currency, source, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [ts, args.time, args.account, args.broker, und, args.contract, "FUT_OPT",
         dt.date.fromisoformat(args.expiry) if args.expiry else None, strike, cp,
         action, args.open_close, args.qty, args.price, args.fee, args.realized_pnl,
         args.exchange or None, "CNY", px, args.iv,
         greeks[0], greeks[1], greeks[2], greeks[3],
         args.margin_local, "CNY" if args.margin_local else None, args.source, args.notes])
    if px and args.sina:
        con.execute("INSERT INTO underlying_ticks VALUES (CAST(current_timestamp AT TIME ZONE 'UTC' AS TIMESTAMP), ?, ?, 'sina', ?)",
                    [args.sina, px, f"fill {args.contract}"])
    rid = con.execute("SELECT MAX(fill_id) FROM fills").fetchone()[0]
    con.close()
    print(f"fill #{rid}: {args.contract} {action} {args.qty}@{args.price} | underlying_px={px} "
          f"| iv={args.iv} delta={None if greeks[0] is None else round(greeks[0],4)}")


if __name__ == "__main__":
    main()
