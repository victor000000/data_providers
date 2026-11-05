"""schwab_clock.py — recover a statement's export clock from the log itself.

PositionStatement and TradeActivity print the instant they were generated, and
`load_schwab_csv.session_offset` recovers the session's UTC offset from that against
the file's mtime. The Account Statement prints no such timestamp, so that recovery
returned zero -- and zero is not neutral: it files a CST (UTC+8) session eight hours
late, on the wrong side of midnight UTC.

Rather than guess the zone from a three-letter abbreviation -- 'CST' is China Standard
Time in this file and Central Standard Time in the next one -- take it from evidence:
find a fill this statement shares with the log and use the difference. 22:12 statement
time against 14:12:56 UTC is an eight hour clock, measured, and re-measured each run.

Nothing matched means None, and the caller must refuse rather than write timestamps it
cannot vouch for.
"""
import datetime as dt
import re

#: A whitelist, not `[A-Z]{2,4}` -- the loose form also matches the account's "SCHW".
ZONES = ("CST", "CDT", "EST", "EDT", "PST", "PDT", "MST", "MDT", "UTC", "GMT", "HKT")

#: A fill the log already has, keyed on everything that identifies the trade.
MATCH = """SELECT ts_utc FROM fills
           WHERE underlying=? AND strike IS NOT DISTINCT FROM ?
             AND call_put IS NOT DISTINCT FROM ? AND action=? AND qty=? AND price=?"""


def offset_from_log(con, fills, tolerance_min=14 * 60):
    """(offset, evidence) for a statement, or (None, None) if nothing matched.

    `offset` is what to SUBTRACT from the statement's naive timestamps to get UTC --
    the same sign as `session_offset`. Rounded to 15 minutes, because every real
    offset is a multiple of it and the statement prints seconds.

    The tolerance is 14 HOURS: the thing being measured IS the offset, so rejecting a
    candidate for being eight hours out rejects the answer -- a 90-minute window matched
    nothing. The six-field key, not a tight window, is what makes a bad match unlikely.
    """
    votes = {}
    for f in fills:
        try:
            row = con.execute(MATCH, [f["underlying"], f["strike"], f["call_put"],
                                      f["action"], f["qty"], f["price"]]).fetchone()
        except Exception:
            return None, None
        if not row or not row[0] or not f.get("ts_utc"):
            continue
        # A fill with no timestamp is not evidence of a clock. Guarded rather than
        # assumed: the caller parses at offset 0 first, and a row that failed to parse
        # must not take the whole batch's clock down with it.
        # `utc = naive - offset`, so offset is (naive - utc) and NOT the reverse. The
        # reverse read UTC-8 for a CST export and moved every fill the wrong way.
        delta = f["ts_utc"] - row[0]
        if abs(delta) > dt.timedelta(minutes=tolerance_min):
            continue
        key = round(delta.total_seconds() / 900.0) * 900
        votes.setdefault(key, []).append((f["underlying"], f["strike"], f["call_put"],
                                          f["action"], str(delta)))
    if not votes:
        return None, None
    # Most votes wins; a tie means the evidence disagrees and nothing should be written.
    best = max(votes.items(), key=lambda kv: len(kv[1]))
    if len(votes) > 1 and sum(1 for v in votes.values() if len(v) == len(best[1])) > 1:
        return None, None
    return dt.timedelta(seconds=best[0]), best[1][0]
