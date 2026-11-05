import datetime as dt

"""cn_expiry.py — when a CN option contract's last trading day actually is.

    CZCE   third trading day counting back INCLUDING the 15th of the prior month
    SHFE   the 5th-to-LAST trading day of the prior month
    DCE    12th trading day of the prior month
    GFEX   5th trading day of the prior month
    CFFEX  index options (MO), third Friday of the CONTRACT month

None of them is the last calendar day of the month before delivery, which is what this
replaced, applied to every venue regardless. Every rule is pinned in
tests/apps/data_ops/test_cn_expiry.py against contracts' measured last traded days.

TRADING DAYS, NOT WEEKDAYS, when a calendar is supplied. The two agree until a CN holiday
week lands inside the prior month, and then they do not: October 2026 opens on the 8th after
Golden Week, so m2611's 12th trading day is the 23rd while counting weekdays gives the 16th.
A week of phantom time-to-expiry is ~15% of IV on a front-month option -- the solver solved
m2611 at 15.37% on the right date while option_bars carried 2026-10-16 for the same contract
and would have priced it at 18.42%. Callers that already hold the exchange calendar should
pass it; callers inside a parse loop without network get the weekday answer, which is a
0-2 day error and never a week.
"""

RULES = {"CZCE": -3, "SHFE": -5, "DCE": 12, "GFEX": 5}


def _trading_days_back(d, n):
    """The n-th weekday strictly before `d`. The offline fallback's counter."""
    while n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _nth_weekday_fwd(year, month, weekday, n):
    """The n-th `weekday` (0=Mon) of the given month. Used for the third-Friday rule."""
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(days=7 * (n - 1))


def _nth_trading_day_fwd(year, month, n):
    """The n-th trading day OF the month -- counting weekdays, not one weekday's occurrences.

    `_nth_weekday_fwd(2026, 8, 0, 5)` is the 5th MONDAY (2026-08-31); the 5th trading day of
    August 2026 is 2026-08-07. DCE's rule is the latter, not the former.
    """
    d = dt.date(year, month, 1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    for _ in range(n - 1):
        d += dt.timedelta(days=1)
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
    return d


def _next_trading_day(d):
    """`d` itself if it is a weekday, else the next one. The unknown-venue fallback only."""
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def _from_calendar(venue, cal, py, pm):
    """The rule counted on real trading days, or None when the calendar does not reach it."""
    days = [d for d in cal if (d.year, d.month) == (py, pm)]
    n = RULES.get(venue, 5)
    if venue == "CZCE":
        days = [d for d in days if d.day <= 15]     # CZCE explicitly includes the 15th
    if len(days) < abs(n):
        return None
    return days[n - 1] if n > 0 else days[n]


def expiry_for(venue, year, month, cal=None):
    """Delivery-month code -> the date the contract actually expires. Rules above."""
    if venue == "CFFEX":
        return _nth_weekday_fwd(year, month, 4, 3)        # third Friday, contract month
    py, pm = (year, month - 1) if month > 1 else (year - 1, 12)
    if cal:
        got = _from_calendar(venue, cal, py, pm)
        if got:
            return got
    if venue == "SHFE":
        # 5 weekdays back from the 1st of the delivery month == the 5th-to-last of the prior
        return _trading_days_back(dt.date(year, month, 1), 5)
    if venue == "CZCE":
        # Start after the anchor because the helper counts strictly backwards.
        return _trading_days_back(dt.date(py, pm, 16), 3)
    if venue == "DCE":
        return _nth_trading_day_fwd(py, pm, 12)           # NOT 5 -- see the measurements
    if venue == "GFEX":
        return _nth_trading_day_fwd(py, pm, 5)
    # Unknown venue: the 15th of the prior month is the closest common rule, and is strictly
    # better than a month-end that is wrong for all of them.
    return _next_trading_day(dt.date(py, pm, 15))
