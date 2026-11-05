import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401
from data_providers.shared import helpguard
helpguard.handle_help(__file__)
"""load_schwab_account.py — Schwab "Account Statement" CSV -> the log of record.

WHY THIS EXISTS. `load_schwab_csv` reads `*PositionStatement.csv` and
`*TradeActivity.csv`; the Account Statement is a third flavour, and the one actually
being saved. Two of them sat unread in `../trading_logs`, so a full session -- the META
strangle and vertical bought back, 18 of 25 TLT strangles closed, a CRWD diagonal
opened and rolled -- was invisible to every profile built on the book. All four
sections land: trade history -> fills, order history -> orders (including CANCELED and
eleven REJECTED for buying power), Options -> broker_positions, summary -> account_daily.

THE PDF IS NOT A SUBSTITUTE: `schwab_account.pdf` is six pages of vector paths -- zero
text, zero images, the glyphs are outlines -- and reading it by eye is how a Dec-18
43 PUT became a 43 CALL four times. Take the .csv.

Idempotent per file (fills and orders dedupe on their notes), so pointing it at the
whole directory picks up only what is new.

    uv run python apps/trade_log/load_schwab_account.py [--dir PATH] [--dry-run]
"""
import argparse, csv, datetime as dt, sys

import duckdb

from data_providers.jobs.trades import load_schwab_csv as sw
from data_providers.jobs.trades import source_dirs
from data_providers.jobs.trades import schwab_writes as W
from data_providers.jobs.trades import schwab_statement as S
from data_providers.jobs.trades import schwab_account_parse as P
from data_providers.jobs.trades import schwab_account_book as B
from data_providers.jobs.trades import schwab_clock_batch as sch_clock
from data_providers.jobs.trades import schwab_report as report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    d = pathlib.Path(a.dir) if a.dir else None
    # source_dirs.find returns STRINGS (glob results) while Path.glob returns Paths;
    # normalise both to Path rather than assuming one shape at every use below.
    files = [pathlib.Path(f) for f in (sorted(d.glob("*AccountStatement.csv")) if d
                                       else source_dirs.find("*AccountStatement.csv"))]
    if not files:
        print("no *AccountStatement.csv found")
        return 1
    files.sort(key=lambda p: (p.stat().st_mtime_ns, p.name))

    # Read-only on a dry run: the anchor still needs to read, but taking the single
    # writer lock is exactly what --dry-run exists to avoid.
    con = duckdb.connect(str(sw.DB), read_only=True) if a.dry_run else duckdb.connect(str(sw.DB))

    # TWO PASSES: parse everything at offset 0, resolve the whole batch's clock, then
    # parse again. Today's statement cannot anchor itself -- see schwab_clock.resolve.
    parsed = {}
    for p in files:
        rows = list(csv.reader(open(p, encoding="utf-8-sig")))
        parsed[p] = (S.sections(rows),
                     sw.session_offset(p, rows[0][0] if rows and rows[0] else ""))
    zero = {p: P.parse_fills(sec.get("Account Trade History", []), dt.timedelta(0))
            for p, (sec, _) in parsed.items()}
    clocks = sch_clock.resolve(con, [(p, parsed[p][0]) for p in files], zero)

    for path in files:
        sec, header = parsed[path]
        offset, how = sch_clock.pick(clocks[path], header)
        if offset is None:
            print(f"{path.name}: CANNOT DETERMINE THE EXPORT CLOCK — no fill in it matches "
                  f"the log and no sibling statement measured one. Refusing to write "
                  f"timestamps that may be hours out.")
            continue
        print(f"{path.name}  (export clock UTC{offset.total_seconds()/3600:+.1f}, {how})")
        fills = (zero[path] if offset == dt.timedelta(0)
                 else P.parse_fills(sec.get("Account Trade History", []), offset))
        orders = P.parse_orders(sec.get("Account Order History", []), offset)
        pos = B.parse_positions(sec.get("Options", []))
        summ = B.parse_summary(sec.get("Account Summary", []))
        report.sections(fills, orders, pos, summ)
        if not a.dry_run:
            from data_providers.jobs.trades.position_costs import statement_day, write_schwab
            got = write_schwab(con, fills, orders, pos, summ, sec.get("Options", []),
                              f"Schwab {path.name} Trade Price", day=statement_day(path))
            print(f"  wrote {got['fills']} new fills, {got['orders']} new orders, "
                  f"{got['positions']} broker positions")
    if con is not None:
        con.close()
    print("dry run — nothing written" if a.dry_run else "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
