import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""build_universe.py — derive the trading universes from the warehouse, and emit the
vendor-specific universe files the downloaders read.

Why this exists as a script rather than a one-off query: the top1000/top3000/etf311 lists
were hand-generated on 2026-10-01 with no record of the filter, so "why is X in the
universe?" had no answer and the families drifted. Everything the downloaders consume is
now derived here, from one stated rule, reproducibly.

Ranking rule: dollar volume (close x volume) summed over the trailing window, requiring a
minimum number of traded days — a name that traded twice in the window is not rankable no
matter how large one print was. Stocks and ETFs are ranked together (the screen trades
both), with the ETF group available separately from the warehouse's own membership set.

Outputs (plans/):
    universe_top1000.txt  universe_top3000.txt  universe_top6000.txt   plain symbols
    universe_etf311.txt                                                 the ETF group
    ibkr_iv_universe_full.txt   IBKR sweep format: bare symbol / IDX:SYM:EXCH / FUT:SYM:EXCH

Usage:
  uv run python apps/data_ops/build_universe.py [--days 90] [--min-days 40]
"""
import argparse
import datetime as dt
import pathlib
import sys

from data_providers.data import clock  # noqa: E402
from data_providers.data import db  # noqa: E402

ROOT = bootstrap.ROOT
PLANS = ROOT / "plans"


def rank(con, since, min_days):
    """-> [(symbol, dollar_volume)] best first, live names only.

    Non-common suffixes are dropped: warrants (-W/-WS), units (-U), rights (-R) and
    preferreds (-P/-PA/-PF...) have thin, discontinuous or non-existent option markets, and
    each would burn a vendor request. This matters at the bottom of the ranking — MET-PF
    (MetLife preferred) sat at rank 4000 on 2026-10-01, and the top6000 tail reaches
    $3M/day, where most names have no listed options at all.

    Dotted symbols are KEPT — BRK.B is a real top-10 equity — but the dot is a Yahoo
    convention and some vendors want a dash instead.
    """
    rows = con.execute("""
        SELECT symbol, sum(close * volume) AS dv, count(*) AS n
        FROM yh.prices
        WHERE date >= ? AND volume IS NOT NULL AND close > 0
        GROUP BY symbol
        HAVING count(*) >= ?
        ORDER BY dv DESC""", [since, min_days]).fetchall()
    out = []
    for sym, dv, _n in rows:
        if is_common(sym):
            out.append((sym, dv))
    return out


def is_common(sym):
    """False for warrants, units, rights and preferreds — none of which carry the options
    this project screens. Yahoo spells them with a hyphen: MET-PF, AHT-PD, BRK-B."""
    s = sym.upper()
    if "-" in s:
        tail = s.split("-", 1)[1]
        if tail[:1] == "P" or tail in ("W", "WS", "U", "R", "RT"):
            return False
        if tail.startswith(("W", "U", "R")) and len(tail) <= 3:
            return False
    if len(s) > 4 and s.endswith(("W", "WS", "U", "R")):
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="trailing window for the ranking")
    ap.add_argument("--min-days", type=int, default=40,
                    help="traded days required inside the window to be rankable")
    args = ap.parse_args()

    con = db.connect(log=None, universe=True)
    since = (clock.today() - dt.timedelta(days=args.days)).isoformat()
    ranked = rank(con, since, args.min_days)
    print(f"{len(ranked):,} rankable symbols over {args.days}d (min {args.min_days} days traded)")

    stamp = f"# Top {{n}} by dollar volume, {since} onward ({args.min_days}+ traded days). "
    for n in (1000, 3000, 6000):
        syms = [s for s, _ in ranked[:n]]
        p = PLANS / f"universe_top{n}.txt"
        p.write_text(f"# Top {n} by dollar volume, {since} onward "
                     f"({args.min_days}+ traded days). Generated "
                     f"{clock.today()} by build_universe.py.\n" + "\n".join(syms) + "\n")
        print(f"  {p.name:<26} {len(syms):>5} symbols  (cutoff "
              f"{ranked[len(syms)-1][1]/1e9:.2f} B/day)" if len(syms) == n else
              f"  {p.name:<26} {len(syms):>5} symbols  (SHORT — only {len(ranked)} rankable)")

    # ETF groups: the warehouse's own membership sets, which is the honest definition.
    # A LOOP, not etf311 hardcoded -- that is how a new group gets a universe file
    # without anyone remembering to come back here. Both are plain `dataset_<slug>`
    # views, so the next group needs only its name added to this tuple.
    for slug in ("etf311", "etf500"):
        try:
            etfs = sorted(r[0] for r in con.execute(
                f"SELECT DISTINCT symbol FROM yh.dataset_{slug} "
                f"WHERE symbol IS NOT NULL").fetchall())
            if not etfs:
                print(f"  {slug} not regenerated: the dataset view is empty")
                continue
            (PLANS / f"universe_{slug}.txt").write_text(
                f"# The full {slug} group (warehouse membership). Generated "
                f"{clock.today()} by build_universe.py.\n" + "\n".join(etfs) + "\n")
            print(f"  {f'universe_{slug}.txt':<26} {len(etfs):>5} symbols  (warehouse membership)")
        except Exception as e:
            print(f"  {slug} not regenerated: {repr(e)[:70]}")

    # IBKR sweep file: keep the index/futures lines (Alpaca cannot cover those), then every
    # ranked name, so one sweep covers breadth and the non-equity families together.
    base = PLANS / "ibkr_iv_universe.txt"
    keep = []
    if base.exists():
        keep = [l.strip() for l in base.read_text().splitlines()
                if l.strip() and not l.startswith("#") and not l.startswith("FUT:")]
    have = {l.split(":")[1] if l.startswith("IDX:") else l for l in keep}
    add = [s for s, _ in ranked[:6000] if s not in have]
    full = PLANS / "ibkr_iv_universe_full.txt"
    full.write_text(
        f"# IBKR TWS IV sweep — indices/futures first (Alpaca cannot cover them), then the\n"
        f"# whole ranked universe. Generated {clock.today()} by build_universe.py.\n"
        f"# Format: SYMBOL (stock/ETF, SMART) · IDX:SYM:EXCH · FUT:SYM:EXCH\n"
        f"# FUT: lines live in ibkr_iv_universe.txt and are NOT copied here — four symbol\n"
        f"# guesses failed on 2026-10-01; the VX contract convention still needs the TWS UI.\n\n"
        + "# --- indices + tier-1 validators ---\n" + "\n".join(keep)
        + "\n\n# --- ranked universe ---\n" + "\n".join(add) + "\n")
    print(f"  {full.name:<26} {len(keep) + len(add):>5} lines ({len(keep)} index/tier-1 "
          f"+ {len(add)} ranked)")
    if len(add) < 6000:
        print(f"  note: {6000 - len(add)} ranked names already present in the base file")
    con.close()


if __name__ == "__main__":
    main()
