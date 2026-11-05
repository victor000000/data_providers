import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""download_cn_option_bars.py — China option EOD bars into market_history.option_bars.

The efficient endpoints, verified 2026-09-30 (the per-day `option_hist_czce` API needs a
Chinese product name and one call per day — these do a year or a contract in one call):

  CZCE  option_hist_yearly_czce(symbol='MA', year='2025')   whole year, ALL contracts,
        with 隐含波动率 (exchange IV), DELTA, 成交量 and 持仓量 (open interest)
  INE   option_hist_shfe(symbol='原油期权', trade_date=…)    per day, SC
  CFFEX option_cffex_zz1000_daily_sina(symbol='mo2610P7400') per contract, MO

Lands in the SAME option_bars table as the US data, so a CN smile uses the identical code path;
exchange volume and open interest go to option_flow_daily as REAL values.

Contract parsing handles each venue's convention (MA502C2225 · mo2610P7400 · sc2612C880).
Idempotent: PK (source, contract, day), ON CONFLICT fills missing columns.

    uv run python apps/data_ops/download_cn_option_bars.py \
        [--czce-years 2024 2025 2026] [--ine-days 120] [--cffex-contracts mo2610P7400 ...]
"""
import argparse
import datetime as dt
import pathlib
import socket
import sys
import time

import pandas as pd

socket.setdefaulttimeout(30)   # akshare requests carry no timeout of their own; never hang

from data_providers.jobs.data_ops.cn_contract import parse_contract  # noqa: E402
from data_providers.data import clock  # noqa: E402
from data_providers.data import db  # noqa: E402
from data_providers.shared.net import retry, status, throttle  # noqa: E402

ROOT = bootstrap.ROOT


def num(v):
    from data_providers.jobs.data_ops.fetch_cn_products import num as finite_number
    return finite_number(v)


def write_frames(con, df, source, product, datecol, codecol, o, h, l, c, vol, oi,
                 code_override=None, date_override=None, iv=None, delta=None, venue=None):
    """One venue frame -> option_bars rows + per-day call/put volume & OI for the flow table."""
    df = df.rename(columns={col: col.strip() for col in df.columns})
    datecol, codecol = datecol.strip(), codecol.strip()
    bars, flow = [], {}
    # Two venues omit a column the others carry: Sina's CFFEX frames have no symbol, and
    # SHFE's per-day frame has no date (the date IS the request parameter). Both are filled
    # from what we already know rather than skipped.
    if code_override and codecol not in df.columns:
        df = df.assign(**{codecol: code_override})
    if date_override is not None and datecol not in df.columns:
        df = df.assign(**{datecol: date_override})
    for _, r in df.iterrows():
        p = parse_contract(r.get(codecol), venue)
        if not p or p[0] != product:
            continue
        prod, exp, cp, strike = p
        try:
            day = pd.to_datetime(r.get(datecol)).date()
        except Exception:
            continue
        close = num(r.get(c)) or num(r.get("今结算")) or num(r.get("结算价"))
        if close is None:
            continue
        v, o_i = num(r.get(vol)), num(r.get(oi))
        bars.append([source, str(r.get(codecol)).strip(), prod, exp, strike, cp, day,
                     num(r.get(o)), num(r.get(h)), num(r.get(l)), close, v, None,
                     venue, product, o_i,
                     num(r.get("今结算")) if "今结算" in df.columns else num(r.get("结算价")),
                     num(r.get(iv)) if iv else None, num(r.get(delta)) if delta else None])
        f = flow.setdefault(day, {"cv": 0.0, "pv": 0.0, "coi": 0.0, "poi": 0.0})
        f["cv" if cp == "C" else "pv"] += v or 0
        f["coi" if cp == "C" else "poi"] += o_i or 0
    frows = [[day, product, source, f["coi"] or None, f["poi"] or None, None, None, None,
                  None, f["cv"] or None, f["pv"] or None,
                  (f["pv"] / f["cv"]) if f["cv"] else None,
                  (f["poi"] / f["coi"]) if f["coi"] else None, None,
                  "exchange-published volume and open interest",
                  f"real OI · {source}"] for day, f in flow.items()]
    from data_providers.jobs.data_ops.cn_frame_store import persist
    persist(con, bars, frows)
    return len(bars), len(flow)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--czce-product", default="MA")
    ap.add_argument("--czce-years", nargs="*", default=["2024", "2025", "2026"])
    ap.add_argument("--ine-days", type=int, default=120)
    ap.add_argument("--cffex-contracts", nargs="*", default=["mo2610C7900", "mo2610P7400"])
    args = ap.parse_args()
    import akshare as ak

    # Write STRAIGHT INTO cn_market, not a staging file bound for market_history.
    #
    # This used staging="akshare", whose merge target is market_history -- which is US-only
    # since the CN split. Every CZCE run since then has been staging 232 MB of methanol,
    # rubber and glass into a file destined for the table where Mastercard lives. The guard
    # added to merge_staging now refuses that fold; this is the actual fix.
    #
    # cn_market.option_bars' first 13 columns are byte-for-byte market_history's order
    # (source, contract, underlying, expiry, strike, call_put, day, open, high, low, close,
    # volume, vwap), so the existing positional INSERT needs no rewriting -- only `mh.`
    # removed, since cn_market is now the main schema rather than an attachment.
    import duckdb as _d
    con = _d.connect(str(ROOT / "cn_market.duckdb"))
    try:
        con.execute(f"ATTACH '{ROOT / 'reference.duckdb'}' AS ref (READ_ONLY)")
    except Exception:
        pass
    total = 0

    print(f"--- CZCE {args.czce_product}: yearly history ---")
    for year in args.czce_years:
        try:
            from data_providers.providers.czce import yearly_options
            df = retry(lambda: yearly_options(symbol=args.czce_product, year=year),
                       tries=3, what=f"czce {args.czce_product} {year}")
            nb, nf = write_frames(con, df, f"akshare_czce_{args.czce_product.lower()}", args.czce_product,
                                  "交易日期", "合约代码", "今开盘", "最高价", "最低价",
                                  "今收盘", "成交量(手)", "持仓量",
                                  iv="隐含波动率", delta="DELTA", venue="CZCE")
            total += nb
            print(f"  {args.czce_product} {year}: +{nb} bars, {nf} flow-days", flush=True)
        except Exception as e:
            print(f"  {args.czce_product} {year}: failed {repr(e)[:70]}", flush=True)
        status("download_cn_option_bars", venue="CZCE", product=args.czce_product,
               bars_total=total)

    print(f"--- INE SC: last {args.ine_days} days ---")
    d = clock.today() - dt.timedelta(days=args.ine_days)
    got = 0
    while args.ine_days > 0 and d <= clock.today():
        # Skip days we already hold: the loop walks a window, so a longer re-run must not
        # re-request stored days. cn_market is the main schema, so this reads it directly.
        have_day = con.execute("""SELECT count(*) FROM option_bars
                                  WHERE source='akshare_ine_sc' AND day=?""", [d]).fetchone()[0]
        if d.weekday() < 5 and not have_day:
            try:
                df = retry(lambda: ak.option_hist_shfe(symbol="原油期权",
                                                       trade_date=d.isoformat()),
                           tries=2, what=f"ine {d}")
                # NB: SHFE frames use 开盘价/收盘价, not CZCE's 今开盘/今收盘 — passing the
                # wrong names silently yields None closes and every row is skipped.
                nb, _ = write_frames(con, df, "akshare_ine_sc", "SC", "交易日期", "合约代码",
                                     "开盘价", "最高价", "最低价", "收盘价", "成交量", "持仓量",
                                     venue="INE", date_override=d)
                got += nb
            except Exception:
                pass
            throttle('akshare')
        d += dt.timedelta(days=1)
    total += got
    print(f"  SC: +{got} bars over {args.ine_days} days")

    print("--- CFFEX MO: the contracts we hold ---")
    for c in args.cffex_contracts:
        try:
            df = retry(lambda: ak.option_cffex_zz1000_daily_sina(symbol=c), tries=3, what=c)
            df = df.reset_index() if "date" not in [str(x).strip() for x in df.columns] else df
            cols = {str(x).strip().lower(): x for x in df.columns}
            nb, _ = write_frames(con, df, "akshare_cffex_mo", "MO",
                                 cols.get("date", "date"), "symbol",
                                 cols.get("open", "open"), cols.get("high", "high"),
                                 cols.get("low", "low"), cols.get("close", "close"),
                                 cols.get("volume", "volume"), cols.get("hold", "no_oi"),
                                 code_override=c, venue="CFFEX")
            total += nb
            print(f"  {c}: +{nb} bars", flush=True)
        except Exception as e:
            print(f"  {c}: failed {repr(e)[:60]}", flush=True)

    # This RUN's products; the old WHERE named ('MA','SC','MO') and reprinted MA every time.
    touched = sorted({args.czce_product.upper(), "SC", "MO"}); ph = ",".join("?" * len(touched))
    for r in con.execute(f"""SELECT underlying, source, count(*), count(DISTINCT expiry),
                                   min(day), max(day) FROM option_bars
                            WHERE underlying IN ({ph}) GROUP BY 1,2 ORDER BY 1""", touched).fetchall():
        print("  ", r)
    n = con.execute(f"SELECT count(*) FROM option_flow_daily WHERE underlying IN ({ph})", touched).fetchone()[0]
    print(f"CN option bars this run: {total} · CN flow-days stored: {n}")
    con.close()


if __name__ == "__main__":
    main()
