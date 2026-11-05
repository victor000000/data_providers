"""calendar.py — CN trading days, month codes, and the expiry the solver measures T to.

Same rules as apps.data_ops.cn_expiry.expiry_for (which counts weekdays, because it runs
inside a parse loop with no network), but counted on the exchange's own trading days here, so
a CN holiday inside the prior month cannot shift T. Falls back to expiry_for when the
calendar does not reach the month.
"""
import datetime as dt

from data_providers.jobs.data_ops.cn_expiry import expiry_for
from data_providers.shared.net import retry


def cn_calendar(ak):
    """The exchanges' own trading days -- holidays included, which weekdays get wrong."""
    df = retry(lambda: ak.tool_trade_date_hist_sina(), what="CN trading calendar")
    return sorted(d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
                  for d in df["trade_date"])


def closed_since(cal, data_day, now_utc):
    """Sessions the calendar says have CLOSED since `data_day` and were never stored.

    Closed, not "due": a session counts only once its calendar day has ENDED in CN (+08:00, no
    DST). Counting today's session from local midnight reported a download gap every morning
    before the market opened -- which is exactly when this board is read, and the same class of
    error as explaining a real gap away with a holiday: a sentence about our data that is not
    about our data.
    """
    cn_today = (now_utc + dt.timedelta(hours=8)).date()
    return [d for d in cal if data_day < d < cn_today]


def nth_trading_day(cal, y, m, n):
    days = [d for d in cal if d.year == y and d.month == m]
    return days[n - 1] if len(days) >= n else None


def expiry_of(venue, y, m, cal):
    """The day this month's options STOP trading, from the exchange calendar.

    Same rules as apps.data_ops.cn_expiry.expiry_for (which counts weekdays because it runs
    inside a parse loop with no network), but counted on real trading days here, so a CN
    holiday inside the prior month cannot shift T by a day. Falls back to expiry_for when the
    calendar does not reach the month.
    """
    if venue == "CFFEX":
        return expiry_for(venue, y, m)
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    if venue == "SHFE":
        days = [d for d in cal if d.year == py and d.month == pm]
        return days[-5] if len(days) >= 5 else expiry_for(venue, y, m)
    if venue == "CZCE":
        # The exchange specification includes the 15th when it is a trading day.
        # Keep this calendar path aligned with the parser's weekday fallback.
        days = [d for d in cal if d.year == py and d.month == pm and d.day <= 15]
        return days[-3] if len(days) >= 3 else expiry_for(venue, y, m)
    if venue == "DCE":
        return nth_trading_day(cal, py, pm, 12) or expiry_for(venue, y, m)
    return nth_trading_day(cal, py, pm, 5) or expiry_for(venue, y, m)


def month_codes(start, end, venue, window, cal):
    """Delivery months whose sampled window [expiry - window, expiry] meets [start, end]."""
    out = []
    y, m = start.year - 1, start.month
    while True:
        e = expiry_of(venue, y, m, cal)
        if e is None or e - dt.timedelta(days=window) > end:
            break
        if e >= start:
            out.append((y, m, e))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        if y > end.year + 1:
            break
    return out


