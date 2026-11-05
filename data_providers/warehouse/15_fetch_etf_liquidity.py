#!/usr/bin/env python3
"""Rank every US-listed ETF by how much money actually trades in it.

WHY A RANKING IS NEEDED AT ALL. The existing `etf311` group is a list the user
supplied by hand. Asking for "the top 500 liquid ETFs" means the membership has to
be DERIVED from a measurement rather than typed, or it is just a second opinion.

LIQUIDITY IS DOLLAR VOLUME, not price and not assets. Close x volume, taken as the
median over the last month: a median because one expiry day or one rebalance makes a
mean meaningless for exactly the funds whose turnover is spiky, and dollars rather
than share count because 10,000 shares of a $400 fund is not the same trade as
10,000 of a $4 one.

THE SCREENER SPANS THE WHOLE US MARKET. Nasdaq's ETF screener is a screener, not a
listing directory: it returns NYSE Arca and Cboe names too (SPY, GLD, IWM, EEM, TLT,
USO, VTI, AGG, LQD are all in it), so one source covers the exchanges the user asked
for without three scrapers that disagree about spelling.

Bars come from Yahoo in batches, cached to disk as they arrive, so a run interrupted
at symbol 4,000 resumes at 4,000 rather than starting over -- the whole sweep is
about ten minutes and the cache is what makes that acceptable to repeat.
"""
import os
import sys

from data_providers.warehouse import config as C  # noqa: E402

from data_providers.warehouse import helpguard  # noqa: E402  (a no-argument script must not
helpguard.handle_help(__file__)  #  do its work because someone asked --help)
from data_providers.warehouse import etf_liquidity as liq  # noqa: E402

TOP_N = 500
LIST = os.path.join(C.SOURCES, "independent", "etf500_user_list.txt")
RANKED = os.path.join(C.SOURCES, "independent", "etf500_ranked.csv")
def write_lists(ranked):
    """The top-N membership list, plus the full ranking as the evidence for it."""
    with open(RANKED, "w") as fh:
        fh.write("rank,symbol,name,dollar_volume_median\n")
        for i, (s, dv, nm) in enumerate(ranked, 1):
            fh.write(f"{i},{s},{nm.replace(',', ' ')},{dv:.0f}\n")
    # `.D`, like etf311_user_list.txt. build_from_list strips it either way, so the two
    # ETF groups are written in the same form -- a regenerated list has to reproduce the
    # file byte for byte or the next run shows a diff that means nothing.
    with open(LIST, "w") as fh:
        for s, _dv, _nm in ranked[:TOP_N]:
            fh.write(f"{s}.D\n")
    return LIST, RANKED


def main():
    names = liq.screener()
    print(f"{len(names)} US-listed ETFs from the screener")
    dv = liq.cached(sorted(names))
    ranked = sorted(((s, v, names[s]) for s, v in dv.items() if v),
                    key=lambda x: -x[1])
    print(f"ranked {len(ranked)} of {len(names)} (the rest have no usable bars)")
    lst, full = write_lists(ranked)
    print(f"  {os.path.relpath(lst, C.ROOT)}  — {min(TOP_N, len(ranked))} symbols")
    print(f"  {os.path.relpath(full, C.ROOT)} — the whole ranking")
    for s, v, nm in ranked[:5]:
        print(f"    {s:6} ${v / 1e6:>9,.0f}M/day  {nm[:44]}")
    print(f"    ... cutoff at rank {TOP_N}: "
          f"${ranked[min(TOP_N, len(ranked)) - 1][1] / 1e6:,.1f}M/day")


if __name__ == "__main__":
    main()
