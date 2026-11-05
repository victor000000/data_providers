import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""build_iv_history_cn_venue.py — daily ATM-IV series for the CN venues that PUBLISH IV.

IVR/IVP for the China book is read from `cn_market.iv_history`. Three builders feed it:

  build_iv_history_cn.py        CZCE/INE, one TRADING DAY at a time (260+ scrapes/product)
  build_iv_history_cn_bulk.py   CZCE, one whole YEAR per request
  THIS FILE                     SHFE/INE `option_vol_shfe`, GFEX `option_hist_gfex`,
                                one PRODUCT-DAY per request

Why this one exists: cn.option_bars held bars for 16 products and cn.iv_history series for 10,
so AL AU CU MO RU ZN were rankable-in-principle and unrankable-in-fact -- their chains were
stored with no IV at all (0 of 1,564,045 rows in option_bars carry one). Both venues publish
their own 隐含波动率, so no model is needed. The parsing, the day-walk and the give-up rule
live in apps/screener/cn_iv/ (this repo caps a file at 100 lines, and this was 269).

Writes DIRECTLY to cn_market.duckdb. Idempotent and resumable: days already stored for a
(product, source) are skipped, so a re-run costs one query instead of 400 requests.

    uv run python apps/screener/build_iv_history_cn_venue.py \
        [--venues SHFE,INE] [--start 2025-01-02] [--end 2026-09-30] [--workers 6]
"""
import argparse
import datetime as dt

import duckdb

from data_providers.jobs.history.cn_iv.dump import write_points
from data_providers.jobs.history.cn_iv.frames import day_from_contract_hist, day_from_series_vol
from data_providers.jobs.history.cn_iv.venue import (SOURCE_SUFFIX, VENUE_ENDPOINT, fetch_days,
                                       progress_writer, report, weekdays)
from data_providers.runtime import ROOT

DB = ROOT / "cn_market.duckdb"
POINT_FN = {"option_vol_shfe": day_from_series_vol, "option_hist_gfex": day_from_contract_hist}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venues", default="SHFE,INE")
    ap.add_argument("--start", default="2025-01-02",
                    help="first day to build; matches the CZCE series' own start")
    ap.add_argument("--end", default=None, help="last day (default: today)")
    ap.add_argument("--workers", type=int, default=6)
    # One --start per venue is a blunt instrument for a backfill: products list years apart
    # (SHFE copper 2018-09, butadiene rubber 2023-07), and every pre-listing day costs a
    # throttled request that returns nothing. Narrowing to one product lets its own listing
    # date bound the walk. Mirrors --products on the bulk and solver builders.
    ap.add_argument("--products", nargs="*", default=None)
    args = ap.parse_args()
    want = {v.strip().upper() for v in args.venues.split(",") if v.strip()}
    only = {p.strip().upper() for p in (args.products or []) if p.strip()}
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()

    import warnings
    import akshare as ak
    warnings.filterwarnings("ignore")

    con = duckdb.connect(str(DB))
    products = con.execute("""SELECT venue, product, ak_symbol FROM cn_product_map
                              WHERE venue = ANY(?) ORDER BY venue, product""",
                           [sorted(want)]).fetchall()
    if only:
        products = [p for p in products if p[1].upper() in only]
    days = weekdays(start, end)
    print(f"{len(products)} products x {len(days)} weekdays  ({start} -> {end})", flush=True)

    wrote = {}
    for venue, prod, ak_sym in products:
        endpoint = VENUE_ENDPOINT.get(venue)
        fn = getattr(ak, endpoint, None) if endpoint else None
        if fn is None:
            print(f"  {venue}:{prod:<4} no endpoint for this venue — skipped", flush=True)
            continue
        src = f"akshare_{prod.lower()}_{SOURCE_SUFFIX[venue]}"
        have = {r[0] for r in con.execute(
            "SELECT day FROM iv_history WHERE underlying=? AND source=?", [prod, src]).fetchall()}
        todo = [d for d in days if d not in have]
        if not todo:
            print(f"  {venue}:{prod:<4} {len(have)} days already stored — skipped", flush=True)
            continue
        points, failed, unfetched = fetch_days(fn, ak_sym, todo, POINT_FN[endpoint],
                                               args.workers, progress_writer(venue, prod))
        print(report(venue, prod, points, failed, unfetched), flush=True)
        if not points:
            continue
        write_points(con, prod, src, points)
        wrote[prod] = len(points)
    con.close()
    print("wrote:", wrote)


if __name__ == "__main__":
    main()
