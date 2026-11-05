"""Validate and atomically merge rolling Sina minute observations."""
import datetime as dt
import math


def store_minute_frame(con, symbol, day_cst, t0, t1, df, full_day=False):
    rows = {}
    night_start = dt.datetime.combine(day_cst - dt.timedelta(days=1), dt.time(21, 0))
    for _, r in df.iterrows():
        ts = dt.datetime.strptime(r["dt"], "%Y-%m-%d %H:%M:%S")
        if full_day:
            if not (night_start <= ts <= dt.datetime.combine(day_cst, dt.time(15, 0))):
                continue
        elif ts.date() != day_cst or not (t0 <= ts.time() <= t1):
            continue
        ts_utc = ts - dt.timedelta(hours=8)
        values = [float(r[k]) for k in ("open", "high", "low", "close", "volume", "hold")]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Non-finite minute bar")
        if values[1] < values[2] or min(values[4:]) < 0:
            raise ValueError("Invalid minute range, volume or open interest")
        rows[ts_utc] = [ts_utc, symbol, *values]
    rows = list(rows.values())
    if not rows:
        return 0
    con.execute("BEGIN")
    try:
        con.execute("""DELETE FROM underlying_minutes WHERE symbol=? AND source='sina_minute'
            AND ts_utc IN (SELECT unnest(?))""", [symbol, [r[0] for r in rows]])
        con.executemany("INSERT INTO underlying_minutes VALUES (?,?,?,?,?,?,?,?, 'sina_minute')",
                        rows)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(rows)

