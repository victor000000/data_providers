"""option_universe.py — WHICH contracts to ask for: OCC symbols, strike ladders, monthlies.

Split out of download_atm_iv.py, which sits at its size ceiling (628 lines against a baseline
of 619 that may grow by 10) and had no room there for the per-contract fetch window. The
functions below are PURE — no network, no database, just arithmetic and a read of the symbol
map — which is exactly what made them worth moving: inline they had no tests at all, and each
one carries a silent-failure note that only a test can hold still.
"""
import datetime as dt
import pathlib
import sys


from data_providers.data import symbols  # noqa: E402


def occ(u, expiry, cp, k):
    # OCC roots contain no hyphen or dot. Yahoo spells class shares BRK-B / CRD-A / TAP-A,
    # but the vendor symbol is BRKB / CRDA / TAPA — passing the Yahoo form through made
    # every class-share batch fail with HTTP 400 "invalid symbol" (~dozens across the top
    # 6000). The stored `underlying` column keeps the Yahoo spelling for warehouse joins;
    # only the constructed contract symbol is normalised.
    #
    # The RULE now comes from the symbol map, not from here. core/symbols.py holds every
    # vendor's spelling of every instrument and derives the OCC root from the canonical
    # entry; core.symbols.canonical_for exists precisely because the downloaders carry YAHOO
    # spellings (BRK-B) while the map is keyed by the IBKR one (BRK B). Its docstring names
    # this file as the reason it was written: reimplementing the translation inline is "two
    # spellings of one rule, free to drift", and a drift is SILENT — a wrong root returns
    # HTTP 400, which the batch loop records as zero bars, indistinguishable from a contract
    # that never traded.
    #
    # canonical_for returns None rather than raising for an unmapped name, so the map is
    # PREFERRED and the local rule is the fallback: a name missing from the CSV must not stop
    # a download. Unknown -> the old behaviour, unchanged.
    canonical = symbols.canonical_for("yahoo", u)
    root = (symbols.occ_root(canonical) if canonical
            else u.replace("-", "").replace(".", "").strip().upper())
    return f"{root}{expiry.strftime('%y%m%d')}{cp}{int(round(k * 1000)):08d}"


def increments(S):
    """Plausible listed strike increments at this price level.

    Three per level, spanning roughly 4x — a coarse ladder alone silently loses the ±2
    strikes: META at $575 was offered only 10s and 25s, so 565 and 585 were never requested
    and the smile came back 2 points wide instead of 5. Over-generating costs nothing (an
    unlisted contract simply returns no bars), so err fine.
    """
    if S < 25:
        return (0.5, 1.0, 2.5)
    if S < 50:
        return (1.0, 2.5, 5.0)
    if S < 200:
        return (2.5, 5.0, 10.0)
    return (5.0, 10.0, 25.0)


def candidates(S0, reach=6):
    """Strikes either side of ATM, per plausible increment.

    `reach` must exceed the requested band width or the OUTER strikes are never requested:
    the band is built from the listed increment, but a strike absent from the candidate set
    is simply missing, and the smile comes back narrower than asked for with no error.
    ±6 covers the ±5 default at every increment.
    """
    out = set()
    for inc in increments(S0):
        base = round(S0 / inc) * inc
        for k in range(-reach, reach + 1):
            k_ = base + k * inc
            if k_ > 0:
                out.add(round(k_, 2))
    return sorted(out)


def third_friday(y, m):
    d = dt.date(y, m, 1)
    fr = [d + dt.timedelta(days=i) for i in range(31)
          if (d + dt.timedelta(days=i)).month == m and (d + dt.timedelta(days=i)).weekday() == 4]
    return fr[2]


def monthly_expiries(start, end):
    out, y, m = [], start.year, start.month
    while True:
        e = third_friday(y, m)
        if e > end:
            break
        if e >= start:
            out.append(e)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out
