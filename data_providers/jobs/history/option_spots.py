"""Broker closes can fill absent daily option-solver inputs without rewriting Yahoo."""
import math


def add_broker_spots(con, symbol, start, end, prices):
    """Return prices plus the days whose inputs are already on an as-traded basis."""
    out, broker_days = dict(prices), set()
    rows = con.execute('''SELECT day,close FROM mhro.underlying_daily
        WHERE symbol=? AND source='ibkr_rth_daily' AND day BETWEEN ? AND ?''',
        [symbol,start,end]).fetchall()
    for day,close in rows:
        key = day.isoformat()
        if key not in out and close is not None and math.isfinite(close) and close>0:
            out[key] = float(close)
            broker_days.add(key)
    return out, broker_days


def historical_close(day, close, splits):
    """Undo later splits on Yahoo's restated close before comparing with old strikes."""
    return close * math.prod(ratio for date,ratio in splits if date>day)
