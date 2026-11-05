import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""record_trade.py — record one fill with full market context into trades.duckdb.

Captures automatically at record time: underlying price, 盘口 (bid/ask),
OI (持仓量), day volume (市场成交量), IV from the fill price AND from mid,
greeks (delta/gamma/theta/vega via Black-Scholes), spread.

Example (US option):
  uv run python record_trade.py \
    --account REPLACE_WITH_IBKR_ACCOUNT --broker IBKR --underlying META --strike 630 --cp P \
    --expiry 2026-11-20 --action SELL --open-close OPEN --qty 2 --price 11.32 \
    --fee 1.04 --exchange NASDAQOM --time "2026-09-29 09:35:21 ET" \
    --order-type LIMIT --order-time "2026-09-29 09:34:50 ET"

China / no-fetch:
  ... --underlying MA --security-type FUT_OPT --no-fetch --currency CNY \
      --margin-local 114507.8 --notes "MA701P2650 sold 快期挂单"
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.request

from zoneinfo import ZoneInfo

import duckdb

from data_providers.compute.bs import implied_vol, bs_greeks, T_years

DB = bootstrap.ROOT / "trades.duckdb"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
from data_providers.shared.rates import R_US as R
ETF = {"TLT", "EWZ", "IEF", "LQD", "DBB", "DSI", "EDV", "IAU", "IXC", "SPXL"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def fallback_from_store(underlying, expiry, strike, cp):
    """Price the fill context from data_providers.data we already hold when the live book is unavailable.

    Found 2026-09-30 by sandbox-testing a record: after hours the live fetch raises, and the
    fill was being written with IV, delta, bid/ask and OI all NULL — losing exactly the
    context that cannot be re-fetched. Read-only and guarded: a locked market_history must
    never stop a fill from being recorded.
    """
    try:
        con = duckdb.connect(str(DB.parent / "market_history.duckdb"), read_only=True)
        bar = con.execute("""SELECT close, day FROM option_bars
                             WHERE underlying=? AND expiry=? AND strike=? AND call_put=?
                             ORDER BY day DESC LIMIT 1""",
                          [underlying, expiry, strike, cp]).fetchone()
        spot_row = con.execute("""SELECT close FROM underlying_daily WHERE symbol=?
                                  ORDER BY day DESC LIMIT 1""", [underlying]).fetchone()
        con.close()
        return ((bar[0] if bar else None), (bar[1] if bar else None),
                (spot_row[0] if spot_row else None))
    except Exception:
        return None, None, None


def clean(v):
    if v is None:
        return None
    s = str(v).replace("$", "").replace(",", "").strip()
    if s in ("", "--", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def spot_of(sym):
    ac = "etf" if sym in ETF else "stocks"
    try:
        d = fetch_json(f"https://api.nasdaq.com/api/quote/{sym}/extended-trading?assetclass={ac}&markettype=pre")
        rows = (d.get("data", {}).get("infoTable", {}) or {}).get("rows") or []
        if rows and rows[0].get("consolidated"):
            px = clean(rows[0]["consolidated"].split()[0])
            if px:
                return px, ac
    except Exception:
        pass
    d = fetch_json(f"https://api.nasdaq.com/api/quote/{sym}/info?assetclass={ac}")
    return clean(d["data"]["primaryData"]["lastSalePrice"]), ac


def book_at(sym, ac, expiry, strike, cp):
    """Fetch the live quote row for one contract: bid/ask/OI/volume.

    No sizes: the Nasdaq option-chain payload carries none (verified live).
    """
    exp_s = expiry.isoformat()
    d = fetch_json(f"https://api.nasdaq.com/api/quote/{sym}/option-chain?assetclass={ac}"
                   f"&fromdate={exp_s}&todate={exp_s}&limit=10000&excode=oprac&callput=callput&money=all&type=all")
    rows = d.get("data", {}).get("table", {}).get("rows", []) or []
    tag = f"{MONTHS[expiry.month-1]} {expiry.day}"
    pre = "c_" if cp == "C" else "p_"
    for row in rows:
        if (row.get("expiryDate") or "").strip() != tag:
            continue
        if clean(row.get("strike")) == strike:
            # SIZES ARE NOT AVAILABLE. This endpoint's headers are exactly
            # {Last, Change, Bid, Ask, Volume, Openinterest} per side -- there is no
            # size field to read (verified against a live response). The old code
            # stored `clean(row.get(pre + "Bid"))` in bid_size, i.e. the bid PRICE,
            # and left ask_size NULL, so the log recorded a price where a size goes.
            # `None` is the honest value; a price in that column is worse than a gap.
            return dict(bid=clean(row.get(pre + "Bid")), ask=clean(row.get(pre + "Ask")),
                        bid_size=None,
                        ask_size=None,
                        oi=clean(row.get(pre + "Openinterest")), volume=clean(row.get(pre + "Volume")))
    return {}


def _et_to_utc(naive_et):
    """Naive ET wall time -> naive UTC, with DST handled by the tz database.

    A flat +4h is correct in EDT and one hour wrong from 2026-11-01 when EST
    begins: every US fill recorded after that would be stored an hour early, in
    the log of record, with nothing to flag it.
    """
    return (naive_et.replace(tzinfo=ZoneInfo("America/New_York"))
                     .astimezone(dt.timezone.utc).replace(tzinfo=None))


def parse_ts(t_orig, account):
    if not t_orig:
        return None
    s = t_orig.strip()
    try:
        if " ET" in s:
            return _et_to_utc(dt.datetime.strptime(s.replace(" ET", ""), "%Y-%m-%d %H:%M:%S"))
        if " CST" in s or " CN" in s or "UTC+8" in s:
            # China Standard Time is UTC+8 year-round (no DST), so a fixed -8 is
            # correct here and is left as-is deliberately.
            return dt.datetime.strptime(s.replace(" CST", "").replace(" CN", "").replace(" UTC+8", ""),
                                        "%Y-%m-%d %H:%M:%S") - dt.timedelta(hours=8)
        val = dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        if account and account.startswith("2010"):
            return val - dt.timedelta(hours=8)      # 浙商期货, CNY wall clock
        return _et_to_utc(val)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--broker", default="")
    ap.add_argument("--underlying", required=True)
    ap.add_argument("--strike", type=float)
    ap.add_argument("--cp", choices=["C", "P"])
    ap.add_argument("--expiry")
    ap.add_argument("--security-type", default="OPT")
    ap.add_argument("--action", required=True, choices=["BUY", "SELL", "BOT", "SLD"])
    ap.add_argument("--open-close", default="")
    ap.add_argument("--qty", type=int, required=True)
    ap.add_argument("--price", type=float, required=True)
    ap.add_argument("--fee", type=float)
    ap.add_argument("--realized-pnl", type=float)
    ap.add_argument("--exchange", default="")
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--time", default="")
    ap.add_argument("--order-type", default="")
    ap.add_argument("--order-time", default="")
    ap.add_argument("--margin-local", type=float)
    ap.add_argument("--margin-currency", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--source", default="live_record")
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()

    action = {"BOT": "BUY", "SLD": "SELL"}.get(args.action, args.action)
    expiry = dt.date.fromisoformat(args.expiry) if args.expiry else None

    # the OCC symbol is built from the parsed columns, never left NULL: it is the join key
    # to option_bars / option_minutes, and a NULL symbol makes the fill unfindable later
    occ = (f"{args.underlying.strip().upper()}{expiry.strftime('%y%m%d')}{args.cp}"
           f"{int(round(args.strike * 1000)):08d}") if (expiry and args.cp and args.strike) else None
    spot = book = None
    iv_fill = iv_mid = None
    greeks = (None, None, None, None)
    ctx = "none"
    if not args.no_fetch and args.strike and args.cp and expiry:
        try:
            spot, ac = spot_of(args.underlying)
            book = book_at(args.underlying, ac, expiry, args.strike, args.cp)
            T = T_years(expiry)
            call = args.cp == "C"
            iv_fill = implied_vol(args.price, spot, args.strike, T, R, call)
            mid = (book["bid"] + book["ask"]) / 2 if book.get("bid") and book.get("ask") else None
            if mid:
                iv_mid = implied_vol(mid, spot, args.strike, T, R, call)
            if iv_fill:
                greeks = bs_greeks(spot, args.strike, T, R, iv_fill, call)
            ctx = f"spot {spot} · book {book.get('bid')}/{book.get('ask')} · oi {book.get('oi')}"
        except Exception as e:
            bar_px, bar_day, s2 = fallback_from_store(args.underlying, expiry, args.strike, args.cp)
            if bar_px and s2:
                spot = s2
                T = T_years(expiry)
                call = args.cp == "C"
                iv_fill = implied_vol(args.price, spot, args.strike, T, R, call)
                if iv_fill:
                    greeks = bs_greeks(spot, args.strike, T, R, iv_fill, call)
                ctx = (f"live fetch failed ({str(e)[:40]}); priced from stored bar "
                       f"{bar_day} close {bar_px} · spot {s2}")
            else:
                ctx = f"fetch failed: {e}"

    bid = book.get("bid") if book else None
    ask = book.get("ask") if book else None
    con = duckdb.connect(str(DB))
    con.execute("""
      INSERT INTO fills (ts_utc, ts_orig, order_ts_utc, order_type, account, broker,
        underlying, symbol, security_type, expiry, strike, call_put, action, open_close,
        qty, price, fee, realized_pnl, exchange, currency,
        bid_at_fill, ask_at_fill, bid_size, ask_size, oi_at_fill, volume_day_at_fill,
        underlying_px, iv_at_fill, iv_mid,
        delta_at_fill, gamma_at_fill, theta_at_fill, vega_at_fill,
        margin_local, margin_currency, source, notes)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [parse_ts(args.time, args.account), args.time, parse_ts(args.order_time, args.account), args.order_type,
         args.account, args.broker, args.underlying, occ, args.security_type, expiry, args.strike, args.cp,
         action, args.open_close, args.qty, args.price, args.fee, args.realized_pnl, args.exchange, args.currency,
         bid, ask, None, None, (book or {}).get("oi"), (book or {}).get("volume"),
         spot, iv_fill, iv_mid, greeks[0], greeks[1], greeks[2], greeks[3],
         args.margin_local, args.margin_currency, args.source, args.notes])
    rid = con.execute("SELECT MAX(fill_id) FROM fills").fetchone()[0]
    con.close()
    f = lambda x: None if x is None else round(x, 4)
    print(f"fill #{rid}: {args.underlying} {args.strike or ''}{args.cp or ''} {action} {args.qty}@{args.price} | "
          f"iv_fill={f(iv_fill)} iv_mid={f(iv_mid)} delta={f(greeks[0])} | {ctx}")


if __name__ == "__main__":
    main()
