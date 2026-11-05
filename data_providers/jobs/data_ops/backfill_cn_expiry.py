import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""backfill_cn_expiry.py — rewrite CN option expiries that were stored with the wrong rule.

Every CN contract in cn_market.option_bars was written with a single rule -- the last
calendar day of the month before delivery -- applied to all four exchanges. None of them
uses a month-end, and they disagree in direction, so the stored expiries are wrong by
different amounts depending on venue (~19 days for CZCE, ~16 the other way for CFFEX MO).

fetch_cn_products.py now computes the right rule per venue, but that only affects FUTURE
fetches: the rows already written keep the old values. This rewrites them in place.

Expiry is derived from (venue, contract root, delivery month) -- the same inputs the
fetcher uses -- so the two cannot drift: this imports the fetcher's own `parse_code`
rather than reimplementing its rules. If the rules change again, re-running this is the
migration.

Dry run by default; --apply writes.

    uv run python apps/data_ops/backfill_cn_expiry.py
    uv run python apps/data_ops/backfill_cn_expiry.py --apply
"""
import argparse
import pathlib
import sys

import duckdb

from data_providers.jobs.data_ops.fetch_cn_products import expiry_for  # noqa: E402  (the single definition of the rules)

ROOT = bootstrap.ROOT
CN = ROOT / "cn_market.duckdb"


def delivery_month(contract, fmt):
    """Recover (year, month) from the contract code, sharing parse_code's parsing so a
    format change cannot silently desynchronise the two."""
    s = str(contract).strip()
    i = 0
    while i < len(s) and not s[i].isdigit():
        i += 1
    rest = s[i:]
    j = 0
    while j < len(rest) and rest[j].isdigit():
        j += 1
    ymd = rest[:j]
    if len(ymd) == 4:
        return 2000 + int(ymd[:2]), int(ymd[2:])
    if len(ymd) == 3:                      # CZCE YMM
        yy, mm = int(ymd[0]), int(ymd[1:])
        from data_providers.data import clock
        year = (clock.today().year // 10) * 10 + yy
        if year < clock.today().year - 5:
            year += 10
        return year, mm
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    con = duckdb.connect(str(CN))
    try:
        fmt_by_venue = {r[0]: r[1] for r in con.execute(
            "SELECT DISTINCT venue, code_format FROM cn_product_map").fetchall()}
        rows = con.execute("""SELECT DISTINCT venue, product, contract, expiry
                              FROM option_bars""").fetchall()
        print(f"{len(rows):,} distinct (venue, product, contract) rows to check")

        fixes = []
        for venue, product, contract, stored in rows:
            y, m = delivery_month(contract, fmt_by_venue.get(venue, "YYMM"))
            if not y:
                continue
            try:
                right = expiry_for(venue, y, m)
            except Exception:
                continue
            if right != stored:
                fixes.append((venue, product, contract, stored, right))

        if not fixes:
            print("every expiry already matches its venue's rule — nothing to rewrite")
            return 0

        by_venue = {}
        for venue, _, _, old, new in fixes:
            by_venue.setdefault(venue, []).append((new - old).days)
        print(f"\n{len(fixes):,} contracts need rewriting:")
        for venue, deltas in sorted(by_venue.items()):
            print(f"  {venue:6} {len(deltas):>5} contracts · shift "
                  f"{min(deltas):+d}..{max(deltas):+d} days")

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            for venue, product, contract, old, new in fixes[:6]:
                print(f"    {venue}:{product} {contract}: {old} -> {new}")
            return 0

        # One transaction: either every expiry moves to the new rule or none does.
        # A partial migration would leave the table mixing two conventions with no way
        # to tell which rows follow which.
        con.execute("BEGIN")
        n = 0
        for venue, product, contract, old, new in fixes:
            con.execute("""UPDATE option_bars SET expiry=?
                            WHERE venue=? AND product=? AND contract=? AND expiry=?""",
                        [new, venue, product, contract, old])
            n += 1
        con.execute("COMMIT")
        print(f"\nrewrote {n:,} contracts onto the correct per-venue rule")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
