import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""ibmarks.py — reading IB ticker fields without believing them.

IB does not leave an unavailable field empty. It leaves NaN, or -1 as a sentinel for
"no value". Both are numbers, both survive `if v:`, and both then flow into IVs, greeks,
scenario ladders and margin requirements as if they were prices -- a strike priced off a
-1 mark produces a confident, wrong answer rather than an error.

Three scripts had grown their own copy of this check. One copy is one place to get it
right.
"""


def clean(v):
    """A usable positive number, or None.

    Rejects None, NaN (the only value not equal to itself), and IB's -1 sentinel -- all
    three of which read as truthy numbers to an unwary caller.
    """
    if v is None or v != v:
        return None
    try:
        return v if v > 0 else None
    except TypeError:
        return None


def mid(ticker):
    """Bid/ask mid, falling back to last then close. None when nothing is usable.

    The fallback order matters: a mid is the only two-sided number here, and a script
    that silently accepts `last` or `close` in its place is marking off a print rather
    than a market. Callers that care about the distinction can call `clean` on the
    fields directly and see which one they got.
    """
    b, a = clean(getattr(ticker, "bid", None)), clean(getattr(ticker, "ask", None))
    if b is not None and a is not None:
        return (b + a) / 2
    return clean(getattr(ticker, "last", None)) or clean(getattr(ticker, "close", None))


def two_sided(ticker):
    """True when the ticker carries a real bid AND ask -- i.e. mid() did not fall back."""
    return clean(getattr(ticker, "bid", None)) is not None and \
        clean(getattr(ticker, "ask", None)) is not None
