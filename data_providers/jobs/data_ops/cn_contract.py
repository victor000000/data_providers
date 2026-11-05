"""cn_contract.py — a CN option contract code -> (product, expiry, call/put, strike).

Split from download_cn_option_bars.py (size ratchet), and it belongs next to cn_expiry: this
resolves the CODE, that resolves the DATE. The expiry used to be the last CALENDAR day of the
month before delivery, which no venue uses — every row written after that rule landed was filed
under the wrong month (MA701 as 2026-12-31 where the inclusive CZCE rule gives 2026-12-11). It stayed hidden
because `expiry` was missing from the upsert's DO UPDATE SET, so re-fetching a day never repaired
one: the rows backfilled to the right value kept it and every newly-INSERTED row got the wrong
one, splitting MA701 across two expiries. Any expiry-keyed smile, DTE or IVR then read half a
series, and the half it read was whichever bucket the query happened to name.
"""
import re

from data_providers.jobs.data_ops.cn_expiry import expiry_for
from data_providers.data import clock

# The second element is the venue the code's FORMAT implies, for callers that do not know it.
# It replaced a strike `scale` that was unpacked on every call and never read.
PATTERNS = [
    (re.compile(r"^([A-Za-z]+)(\d{3,4})([CP])(\d+)$"), "CZCE"),      # CZCE/INE: MA502C2225
    (re.compile(r"^([A-Za-z]+)(\d{3,4})-([CP])-(\d+)$"), "CFFEX"),   # CFFEX dashed
]


def parse_contract(code, venue=None):
    """-> (product, expiry, call_put, strike), or None.

    `venue` overrides the format's guess, and is required for INE and CFFEX: MO2610P7400 has no
    dashes so it matches the CZCE pattern, and SC2612C880 is CZCE-shaped but settles on INE rules.
    """
    for pat, code_venue in PATTERNS:
        m = pat.match(str(code).strip())
        if not m:
            continue
        prod = m.group(1).upper()
        yymm = m.group(2)
        if len(yymm) == 3:
            # CZCE codes are YMM — a SINGLE-digit year: MA402 is 2024-02, not 2004-02.
            # Parsing it as YYMM gave yy=4 -> year 2004, so all 152,304 CZCE rows carried
            # expiries 2004-2007 while their trading days were 2024-2026 (live evidence,
            # market_history.option_bars, source akshare_czce_ma). Every CN DTE, smile,
            # IVR/IVP and expiry join built on them was off by twenty years. Resolve the
            # digit against the CURRENT decade, rolling forward when it lands in the past.
            yy, mm = int(yymm[0]), int(yymm[1:])
            base = (clock.today().year // 10) * 10
            year = base + yy
            if year < clock.today().year - 5:
                year += 10
        else:
            yy, mm = int(yymm[:-2] or 0), int(yymm[-2:])
            year = 2000 + yy if yy else clock.today().year
        return prod, expiry_for(venue or code_venue, year, mm), m.group(3), float(m.group(4))
    return None
