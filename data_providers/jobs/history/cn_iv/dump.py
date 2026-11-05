"""dump.py — fetch to JSON, import to the database, as two separate commands.

Why they are separate: two jobs cannot both hold cn_market.duckdb (DuckDB takes an exclusive
file lock) and the fetch is 99% of the wall clock here. Splitting them lets a fetch run beside
another writer -- the akshare rate gate is shared across processes precisely so two fetchers
can interleave -- and the write happens later, in one transaction.

cn_product_map's ak_symbol is duplicated here as SOLVER_NAMES so a --dump run can fetch
WITHOUT opening the database at all.
"""
import datetime as dt
import json

from data_providers.jobs.history.cn_iv import calendar, chains, product
from data_providers.data import clock

SOLVER_NAMES = {
    "DCE": {"I": "铁矿石期权", "M": "豆粕期权", "C": "玉米期权",
            "PG": "液化石油气期权", "PP": "聚丙烯期权"},
    "CFFEX": {"MO": "mo"},        # the CFFEX chain comes from the month code; name unused
    # GFEX goes through Sina too: its own endpoint refuses after ~20 requests (twice on
    # 2026-10-07), so it offers no bulk history at any pace.
    "GFEX": {"SI": "工业硅期权", "LC": "碳酸锂期权"},
}


def add_args(ap):
    """The dump/import flags, next to the code that implements them."""
    ap.add_argument("--dump", metavar="FILE", help="fetch only, write JSON, no database")
    ap.add_argument("--import", dest="import_from", metavar="FILE",
                    help="write a --dump file into iv_history and exit")


def _write(path, out, calls):
    # Written after EVERY product, not once at the end: a full sweep is ~2,500 requests and
    # half an hour, and the first version held it all in memory until the last product -- so
    # a Ctrl-C threw away every point already paid for. (It did: no file at all.)
    with open(path, "w") as f:
        json.dump({"points": out, "rate": product.solve.CN_RATE, "calls": calls,
                   "generated": clock.now().isoformat(timespec="seconds")}, f, indent=1)


def dump_run(args, ak, cal):
    """Fetch only, into JSON: {product: {day: iv}}. No database is opened."""
    args.synthetic = getattr(args, "synthetic", None) or {}
    out, calls = {}, 0
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else clock.today()
    for venue in [v.strip().upper() for v in args.venues.split(",") if v.strip()]:
        for prod in args.products or sorted(SOLVER_NAMES.get(venue, {})):
            prod = prod.upper()
            name = SOLVER_NAMES.get(venue, {}).get(prod)
            if name is None:
                print(f"  {venue}:{prod}: unknown product for this venue — skipped", flush=True)
                continue
            path = chains.underlying_path(ak, venue, prod)
            months = calendar.month_codes(start, end, venue, args.window, cal)
            if args.limit_months:
                months = months[-args.limit_months:]
            points, calls_p, _bars = product.fetch_product(args, ak, venue, prod, name, path,
                                                           months, end, workers=args.workers)
            out[prod] = {str(d): v for d, v in sorted(points.items())}
            calls += calls_p
            _write(args.dump, out, calls)
            print(f"  {venue}:{prod:<3} {len(points)} points ({calls_p} quote calls)",
                  flush=True)
    print(f"dumped {sum(len(v) for v in out.values())} points to {args.dump} "
          f"({calls} quote calls)", flush=True)


def write_points(con, prod, src, points):
    """One product's points into iv_history. Idempotent: DELETE-then-INSERT per (day, source).

    The empty guard is load-bearing, not defensive: a --bars run over a product whose IV was
    already stored passes NO points on purpose (the bars are the thing being written), and
    DuckDB raises on executemany([]). Bound `rows` is load-bearing too: check_regressions
    reads the guard's name from a bare list-comp, and `x.items()` hides it behind an attribute.
    """
    rows = sorted(points.items())
    if not rows:
        return 0
    con.execute("BEGIN")
    con.executemany("DELETE FROM iv_history WHERE day=? AND underlying=? AND source=?",
                    [(d, prod, src) for d, _v in rows])
    con.executemany("INSERT INTO iv_history SELECT ?, ?, NULL, ?, (SELECT close FROM futures_daily WHERE product=? AND day=? AND source IN ('sina_main','sina_index')), ?",
                    [(d, prod, v, prod, d, src) for d, v in rows])
    con.execute("COMMIT")



def import_run(args, con):
    """Write a --dump file into iv_history."""
    blob = json.load(open(args.import_from))
    wrote = {}
    for prod, days in blob.get("points", {}).items():
        points = {dt.date.fromisoformat(d): v for d, v in days.items()}
        if points:
            write_points(con, prod, f"akshare_{prod.lower()}_solver", points)
            wrote[prod] = len(points)
    print("imported:", wrote)
