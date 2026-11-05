import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""build_cn_futures.py — the UNDERLYING daily series for every CN product.

`cn.futures_daily` was designed with a full schema and never populated: 1,756,914 option rows
sat beside a table with zero rows, and `iv_history.spot` is NULL on all 9,851. Nothing fetched
a CN future, so the book could rank implied vol but not compute realized vol -- no VRP, no
moneyness, and no marking a CN position from stored data.

ONE REQUEST PER PRODUCT: Sina's main-continuous series (`SR0`, `M0`, ...) is the front-month
roll, published as one daily history back to listing -- 22 calls, versus ~900 for every
contract month. Stored under the continuous symbol, because it IS a roll and not a deliverable;
a consumer needing a specific month must fetch that month. CFFEX MO is the exception: its
underlying is the 中证1000 INDEX, so it gets the index series.

Idempotent: DELETE-then-INSERT per (venue, product, contract).
    uv run python apps/screener/build_cn_futures.py [--products SR M ...]
"""
import argparse
import datetime as dt
import pathlib
import sys

import duckdb

from data_providers.jobs.history.cn_iv.underlying import index_series, main_series
from data_providers.runtime import ROOT
from data_providers.shared.net import throttle

DB = ROOT / "cn_market.duckdb"
COLS = ("venue, product, contract, day, open, high, low, close, settle, volume, oi, source")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--products", nargs="*", default=None)
    args = ap.parse_args()

    import warnings
    import akshare as ak
    warnings.filterwarnings("ignore")

    con = duckdb.connect(str(DB))
    products = con.execute("""SELECT venue, product FROM cn_product_map ORDER BY product""").fetchall()
    if args.products:
        keep = {p.upper() for p in args.products}
        products = [p for p in products if p[1].upper() in keep]
    print(f"{len(products)} products", flush=True)

    wrote = {}
    for venue, prod in products:
        throttle("akshare")
        try:
            rows = index_series(ak) if venue == "CFFEX" else main_series(ak, prod)
        except Exception as e:                                    # noqa: BLE001
            print(f"  {venue}:{prod:<4} FAILED {type(e).__name__}: {str(e)[:50]}", flush=True)
            continue
        if not rows:
            print(f"  {venue}:{prod:<4} no rows", flush=True)
            continue
        contract = "000852" if venue == "CFFEX" else f"{prod.upper()}0"
        src = "sina_index" if venue == "CFFEX" else "sina_main"
        con.execute("BEGIN")
        con.execute("DELETE FROM futures_daily WHERE venue=? AND product=? AND contract=?",
                    [venue, prod, contract])
        con.executemany(f"INSERT INTO futures_daily ({COLS}) VALUES ({','.join(['?'] * 12)})",
                        [(venue, prod, contract, *r, src) for r in rows])
        con.execute("COMMIT")
        wrote[prod] = (len(rows), str(rows[-1][0]))
        print(f"  {venue}:{prod:<4} {len(rows):>5} days -> {contract} to {rows[-1][0]}", flush=True)
    con.close()
    print("wrote:", wrote)


if __name__ == "__main__":
    main()
