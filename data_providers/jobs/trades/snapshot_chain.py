import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""snapshot_chain.py — snapshot a full option chain (盘口, IV, greeks, OI, volume) into trades.duckdb.

Usage:
  uv run python snapshot_chain.py META CRWD ILMN SWKS TLT EWZ --expiry 2026-11-20
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.request

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


def spot_of(sym, ac):
    try:
        d = fetch_json(f"https://api.nasdaq.com/api/quote/{sym}/extended-trading?assetclass={ac}&markettype=pre")
        rows = (d.get("data", {}).get("infoTable", {}) or {}).get("rows") or []
        if rows and rows[0].get("consolidated"):
            px = clean(rows[0]["consolidated"].split()[0])
            if px:
                return px
    except Exception:
        pass
    d = fetch_json(f"https://api.nasdaq.com/api/quote/{sym}/info?assetclass={ac}")
    return clean(d["data"]["primaryData"]["lastSalePrice"])


def snapshot(sym, expiry):
    ac = "etf" if sym in ETF else "stocks"
    spot = spot_of(sym, ac)
    exp_s = expiry.isoformat()
    d = fetch_json(f"https://api.nasdaq.com/api/quote/{sym}/option-chain?assetclass={ac}"
                   f"&fromdate={exp_s}&todate={exp_s}&limit=10000&excode=oprac&callput=callput&money=all&type=all")
    rows = d.get("data", {}).get("table", {}).get("rows", []) or []
    tag = f"{MONTHS[expiry.month-1]} {expiry.day}"
    T = T_years(expiry)
    con = duckdb.connect(str(DB))
    snap_id = con.execute("""INSERT INTO chain_snapshots (ts_utc, underlying, expiry, spot, source)
        VALUES (CAST(current_timestamp AT TIME ZONE 'UTC' AS TIMESTAMP), ?, ?, ?, 'nasdaq_chain') RETURNING snap_id""",
        [sym, expiry, spot]).fetchone()[0]
    n = 0
    for row in rows:
        if (row.get("expiryDate") or "").strip() != tag:
            continue
        K = clean(row.get("strike"))
        if not K or not spot:
            continue
        for side, pre, call in (("C", "c_", True), ("P", "p_", False)):
            bid = clean(row.get(pre + "Bid"))
            ask = clean(row.get(pre + "Ask"))
            mid = (bid + ask) / 2 if (bid and ask) else None
            iv = delta = gamma = theta = vega = None
            if mid:
                iv = implied_vol(mid, spot, K, T, R, call)
                if iv:
                    delta, gamma, theta, vega = bs_greeks(spot, K, T, R, iv, call)
            con.execute("INSERT INTO chain_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [snap_id, sym, expiry, K, side, bid, ask, None, None, mid, iv,
                         delta, gamma, theta, vega,
                         clean(row.get(pre + "Openinterest")), clean(row.get(pre + "Volume")), spot])
            n += 1
    con.close()
    print(f"{sym} {exp_s}: snapshot #{snap_id}, spot={spot}, {n} contracts stored")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--expiry", "--expiries", dest="expiry", default="2026-11-20",
                    help="comma-separated list for term structure, e.g. 2026-10-16,2026-11-20,2026-12-18")
    args = ap.parse_args()
    expiries = [dt.date.fromisoformat(x.strip()) for x in args.expiry.split(",") if x.strip()]
    for s in args.symbols:
        for expiry in expiries:
            try:
                snapshot(s.upper(), expiry)
            except Exception as e:
                print(f"{s} {expiry}: FAILED — {e}")


if __name__ == "__main__":
    main()
