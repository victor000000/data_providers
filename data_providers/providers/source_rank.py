"""source_rank.py — how much a quote source is trusted, in one place.

WHY THIS EXISTS. `SOURCE_RANK` lived in fetch_extended_hours_prices.py, and
build_positions.py could not import it: that module pulls in ib_async, futu-api
and alpaca-py at import time, which is a heavy price for a rank table. So
build_positions hand-copied it as `SPOT_SOURCE_RANK` with a comment saying
"keep the two in step" -- a comment is not a mechanism, and the two decide which
price the entire book is valued at. They are one table now.

Dependency-free on purpose: this is imported by the fetch layer, the pricing
layer and the tests, and must cost nothing to import.

LOWER IS BETTER. TWS is the direct broker feed and the only source with a
genuine two-sided book outside regular hours; Futu publishes a snapshot with no
book at all. The order matters most when sources disagree, which is exactly when
nobody is watching.
"""

SOURCE_RANK = {
    "TWS": 1,        # direct broker feed — a real exchange book
    "Tiingo": 2,     # IEX feed; a real two-sided quote during RTH
    "Alpaca": 3,      # configured, IEX-only; single-venue, so it can be far off
    "ThetaData": 4,  # EOD only on the free tier
    "yfinance": 5,   # scraped; produced the bogus TLT print of 2026-10-06
    "Futu": 6,       # snapshot only, no book
}

# How old a two-sided quote may be and still count as real-time. One value, used
# by both the fetch layer's classifier and the pricing layer's ordering, so
# "real-time" cannot mean two different ages in two files.
LIVE_MAX_AGE_MINUTES = 15


def aged_class(quote_class, is_realtime, age_minutes):
    """(class, is_realtime) as they are NOW, not as they were when the quote was written.

    A QUOTE IS LIVE ONLY WHILE IT IS RECENT, and `quote_class`/`is_realtime` are stamped
    by the fetch at capture time. Reading them back later without looking at the clock
    reports what the quote WAS. Measured on 2026-10-06: `fetch_extended_hours_prices`
    last ran at 13:46, TWS went down, and the 17:20 build took its spots from that table
    anyway -- four underlyings priced off a 214-minute-old quote, every one of them still
    labelled `live` with `is_realtime=True`, so the report said "every underlying priced
    in real time" while CRWD had moved 1.53% since the price the whole book was struck at.

    The query's freshness clause already ranks recent quotes first; it cannot help when
    nothing recent exists, and it never touched the label. This is the label.

    A class that is already not real-time is returned untouched -- there is nothing to
    downgrade, and overwriting `last_only` with `live_stale` would lose the distinction
    between "a stale book" and "no book at all".
    """
    if not is_realtime or age_minutes is None:
        return quote_class, is_realtime
    if age_minutes <= LIVE_MAX_AGE_MINUTES:
        return quote_class, True
    return "live_stale", False
