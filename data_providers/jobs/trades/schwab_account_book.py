"""schwab_account_book.py — the account's own state, from one Account Statement.

Two sections that describe the ACCOUNT rather than the trading: the `Options` table
(the broker's current positions, which `broker_positions` is reconciled against) and
`Account Summary` (net liquidating value and buying power, the settlement truth for
the day).
"""
import datetime as dt

from data_providers.jobs.trades import load_schwab_csv as sw
from data_providers.jobs.trades.schwab_account_parse import ACCOUNT, occ

def parse_positions(rows):
    """The `Options` table = the broker's own current list, which is what `positions` is
    reconciled against. A zero qty row is a leg closed today and is skipped."""
    out = []
    stamp = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for r in rows:
        r = list(r) + [""] * (9 - len(r))
        sym = r[0].strip()
        if not sym or sym.upper() == "OVERALL TOTALS":
            continue
        exp = sw.parse_expiry(r[2])
        typ = r[4].strip().upper()
        qty = sw.money(r[5]) or 0.0
        if not exp or typ not in ("PUT", "CALL") or qty == 0:
            continue
        out.append([stamp, ACCOUNT, "Schwab", sym, "OPT",
                    occ(sym, exp, r[3], typ[0]), exp, sw.money(r[3]), typ[0], "USD", qty])
    return out


def parse_summary(rows):
    """Net liquidating value + buying power. The buying power numbers are NEGATIVE in
    this statement (the account is over-allocated), and are stored as printed."""
    got = {}
    for r in rows:
        if len(r) >= 2 and r[0].strip():
            got[r[0].strip()] = sw.money(r[1])
    return got
