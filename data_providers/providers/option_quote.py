"""Small provenance-preserving option quote contract for interactive consumers."""
import math
import re
from datetime import datetime, timezone


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def contract(symbol):
    match = re.fullmatch(r'([A-Z]{1,6})(\d{6})([CP])(\d{8})', symbol)
    if not match:
        raise ValueError('Expected a standard US OCC option symbol')
    expiry = datetime.strptime(match[2], '%y%m%d').date().isoformat()
    return dict(symbol=symbol, underlying=match[1], expiry=expiry, right=match[3], strike=int(match[4])/1000)


def quote(symbol, bid=None, ask=None, bid_size=None, ask_size=None, *, source, feed,
          quote_time=None, received_at=None, **extra):
    received_at = received_at or datetime.now(timezone.utc).isoformat()
    bid, ask = number(bid), number(ask)
    age = None
    future = False
    if quote_time:
        try:
            stamp = datetime.fromisoformat(str(quote_time).replace('Z', '+00:00'))
            if stamp.tzinfo is not None:
                elapsed = (datetime.now(timezone.utc) - stamp).total_seconds()
                future = elapsed < -5
                age = max(0, elapsed)
        except ValueError:
            pass
    valid = bid is not None and ask is not None and ask > 0 and bid <= ask
    state = 'future_timestamp' if future else 'missing' if bid is None or ask is None else 'crossed' if bid > ask else 'stale' if age is not None and age > 30 else feed
    if feed == 'indicative' and state not in ('missing', 'crossed', 'future_timestamp'):
        state = 'indicative'  # Age is still reported separately; never label modified quotes live.
    return dict(symbol=symbol, bid=bid, ask=ask, bid_size=number(bid_size), ask_size=number(ask_size),
                midpoint=(bid+ask)/2 if valid else None, source=source, feed=feed, state=state,
                quote_time=str(quote_time) if quote_time else None, received_at=received_at, age_seconds=age,
                executable_reference=valid and not future and feed in ('opra', 'live') and age is not None and age <= 30, **extra)


class SourceUnavailable(ValueError):
    """Provider-authored public diagnostic, excluding upstream credential-bearing errors."""
    def __init__(self, detail):
        self.safe_detail = detail
        super().__init__(detail)
