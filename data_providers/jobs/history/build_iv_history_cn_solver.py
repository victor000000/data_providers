import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""build_iv_history_cn_solver.py — IV series + bars for the CN venues whose own IV we cannot
read: DCE (HTTP 412 bot wall), CFFEX MO (no IV column), GFEX (refuses bulk). The IV is OURS:
ATM strike from the chain, solved by compute/bs.py's QuantLib — checked 2026-10-07 against
CZCE's published IV at 0.26 vol points.  [--bars --pad 7] | --dump f | --import f"""
import argparse
import datetime as dt

import duckdb

from data_providers.jobs.history.cn_iv import calendar, chains, dump, fetch, product, solve
from data_providers.jobs.history.cn_iv.bars import covered_contracts, save_product
from data_providers.jobs.history.cn_iv.dump import SOLVER_NAMES, dump_run, import_run
from data_providers.jobs.history.cn_iv.product import WINDOW_DAYS
from data_providers.runtime import ROOT
from data_providers.data import clock

DB = ROOT / "cn_market.duckdb"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venues", default="DCE,CFFEX")
    ap.add_argument("--products", nargs="*", default=None)
    ap.add_argument("--start", default="2025-01-02")
    ap.add_argument("--end", default=None)
    ap.add_argument("--window", type=int, default=WINDOW_DAYS)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit-months", type=int, default=0)
    ap.add_argument("--db", default=str(DB), help="smoke test on a copy")
    ap.add_argument("--bars", action="store_true", help="also store option bars")
    ap.add_argument("--synthetic", nargs="*", metavar="PROD=STEP", help="e.g. PP=100")
    ap.add_argument("--pad", type=int, default=solve.PAD_STRIKES, help="strikes either side")
    ap.add_argument("--max-strikes", type=int, default=solve.MAX_STRIKES)
    dump.add_args(ap)              # --dump / --import
    args = ap.parse_args()
    want = {v.strip().upper() for v in args.venues.split(",") if v.strip()}
    args.synthetic = {k.upper(): float(v) for k, v in
                      (s.split("=") for s in (args.synthetic or []))}
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else clock.today()
    if args.import_from:
        # Before akshare, before the calendar: a dump import is a DB job, never a network one.
        con = duckdb.connect(args.db)
        import_run(args, con)
        con.close()
        return

    import warnings
    import akshare as ak
    warnings.filterwarnings("ignore")
    cal = calendar.cn_calendar(ak)
    if args.dump:
        dump_run(args, ak, cal)
        return

    con = duckdb.connect(args.db)
    products = con.execute("""SELECT venue, product, ak_symbol, code_format FROM cn_product_map
                              WHERE venue = ANY(?) ORDER BY venue, product""",
                           [sorted(want)]).fetchall()
    if args.products:
        products = [p for p in products if p[1].upper() in {x.upper() for x in args.products}]
    print(f"{len(products)} products, window {args.window}d, {start} -> {end}", flush=True)
    wrote = {}
    anchor = con.execute("SELECT max(day) FROM option_bars").fetchone()[0] - dt.timedelta(days=5)
    for venue, prod, name, fmt in products:
        src = f"akshare_{prod.lower()}_solver"
        have = {r[0] for r in con.execute("SELECT day FROM iv_history WHERE underlying=? "
                                          "AND source=?", [prod, src]).fetchall()}
        path = fetch.path_or_none(lambda: chains.underlying_path(ak, venue, prod),
                                  f"{venue}:{prod:<3}")
        if path is None:
            continue
        months = calendar.month_codes(start, end, venue, args.window, cal)[-args.limit_months:]  # 0 = all
        print(f"  {venue}:{prod:<3} {len(path)} days, {len(months)} months, {len(have)} stored", flush=True)
        # Narrow to un-stored contracts ONLY while this product's IV series is CURRENT: while
        # it lags, the strikes the solve needs ARE the stored ones (2026-10-11, nine DCE
        # products a session behind: 0 points narrowed, 1 each unnarrowed).
        known = covered_contracts(con, venue, prod) if (args.bars and have and max(have) >= anchor) else None
        points, calls, bars = product.fetch_product(
            args, ak, venue, prod, name, path, months, end, args.workers, args.bars, known)
        points = {d: v for d, v in points.items() if d not in have}
        if not points and not bars:
            print(f"  {venue}:{prod:<3} nothing new", flush=True)
            continue
        save_product(con, venue, prod, fmt, src, points, bars if args.bars else None, cal)
        wrote[prod] = len(points)
        print(f"  {venue}:{prod:<3} {len(points):>4} points -> {src} ({calls} calls"
              f"{f', {len(bars)} bar rows' if args.bars else ''})", flush=True)
    con.close()
    print("wrote:", wrote)


if __name__ == "__main__":
    main()
