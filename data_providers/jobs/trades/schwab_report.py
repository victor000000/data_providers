"""schwab_report.py — what a loaded statement prints.

Split out of `load_schwab_account` for the size limit, and worth having on its own:
the fill list is the only place a reader can SEE whether the parse is right. It is
what caught the two bugs this loader shipped with -- every price 100x too high
(`.27` read as 27.0 because the number pattern needed a digit before the point) and
every spread missing half its legs (the second leg prints with a blank exec time).

Anything that makes the parse wrong makes this list look wrong too. Print it always.
"""


def sections(fills, orders, pos, summ):
    print(f"  fills {len(fills)} · orders {len(orders)} · positions {len(pos)} · "
          f"net liq {summ.get('Net Liquidating Value')}")
    for f in fills:
        print(f"    {f['ts_utc']:%m-%d %H:%M} {f['underlying']:5} {f['strike']:>7g}"
              f"{f['call_put']} {f['action']:4} {f['qty']:>3} @ {f['price']:>6} "
              f"{f['open_close']}")
