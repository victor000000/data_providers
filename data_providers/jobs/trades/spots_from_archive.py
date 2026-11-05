"""spots_from_archive.py — the fill-time spot from `underlying_minutes`.

WHY THIS EXISTS. `backfill_us_spots` reads yfinance 5-minute bars, which reach back
five days and no further. Every fill older than that silently kept a NULL
`underlying_px` -- ten journal option fills from 2026-09-29 among them, with a strike,
a price and an expiry, and no underlying price to solve an IV against. yfinance was
never the right source for those anyway: `market_history.underlying_minutes` holds
1-minute bars from 2024-01 with the extended session included, which is finer than
5-minute AND already on disk.

It returns (spot, source) so the caller can record where the number came from. A
minute bar is the CLOSE of the bar at or before the fill instant -- the same
nearest-prior convention the yfinance path uses, so the two agree where they overlap.
"""
import datetime as dt

#: The last bar at or before the instant. Naive-UTC stamps, as stored throughout.
QUERY = """
SELECT close FROM underlying_minutes
 WHERE symbol = ? AND ts_utc <= ? AND ts_utc >= ?
 ORDER BY ts_utc DESC LIMIT 1
"""


def spot_at(con, underlying, ts_utc, max_lookback_min=45):
    """(spot, source) at `ts_utc`, or (None, None) if the archive has nothing near it.

    The lookback is bounded because a bar from hours earlier is not "the price when you
    traded" -- it is a different session's price wearing the right label. Better to
    return nothing, which the caller records as missing, than a plausible wrong number.
    """
    lo = ts_utc - dt.timedelta(minutes=max_lookback_min)
    row = con.execute(QUERY, [underlying, ts_utc, lo]).fetchone()
    if not row or row[0] is None:
        return None, None
    return float(row[0]), "underlying_minutes_1m"


def attach(con, log_con, where="underlying_px IS NULL", limit=None):
    """Fill `underlying_px` for log fills the archive can reach. Returns the count.

    `con` is market_history (read-only fine); `log_con` is the log of record. A single
    UPDATE per fill rather than a batch: these are tens of rows and the guard that
    matters is the empty-list one, which a per-row loop cannot hit.
    """
    sql = ("SELECT fill_id, underlying, ts_utc FROM fills "
           f"WHERE {where} AND ts_utc IS NOT NULL AND underlying IS NOT NULL "
           "ORDER BY fill_id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = log_con.execute(sql).fetchall()
    n = 0
    for fid, underlying, ts in rows:
        spot, _src = spot_at(con, underlying, ts)
        if spot is None:
            continue
        log_con.execute("UPDATE fills SET underlying_px=? WHERE fill_id=?", [spot, fid])
        n += 1
    return n, len(rows)
