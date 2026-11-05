"""Capture actual one-minute underlying sessions for all recorded US fills."""
import datetime as dt
import math
from concurrent.futures import ThreadPoolExecutor,as_completed
from data_providers.jobs.history.download_atm_iv import write_rows


def mode_fill_underlyings(con, fetch_range, status, workers=4):
    sessions=con.execute('''WITH fills AS (
        SELECT DISTINCT underlying,CAST(ts_utc AS DATE) AS day FROM tr.fills
        WHERE broker IN ('IBKR','Schwab') AND underlying IS NOT NULL
          AND ts_utc>=DATE '2024-02-01'), held AS (
        SELECT symbol,CAST(ts_utc AS DATE) AS day FROM mh.underlying_minutes
        WHERE source='alpaca_1min' UNION
        SELECT symbol,CAST(ts_utc AS DATE) AS day FROM mhro.underlying_minutes
        WHERE source='alpaca_1min')
        SELECT f.underlying,f.day FROM fills f ANTI JOIN held h
          ON f.underlying=h.symbol AND f.day=h.day ORDER BY f.day,f.underlying''').fetchall()
    print(f'fill underlying sessions: {len(sessions)} need actual1-minute bars',flush=True)
    failures=total=0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(fetch_range,symbol,day,day):(symbol,day) for symbol,day in sessions}
        for future in as_completed(pending):
            symbol,day=pending[future]
            try:
                rows=[]
                for bar in future.result():
                    when=dt.datetime.fromisoformat(bar['t'].replace('Z','+00:00')).astimezone(dt.timezone.utc).replace(tzinfo=None)
                    prices=[bar[k] for k in ('o','h','l','c')];volume=bar['v']
                    if when.date()!=day or not all(math.isfinite(v) and v>0 for v in prices) or not math.isfinite(volume) or volume<0:
                        raise ValueError('Invalid fill-session minute bar')
                    rows.append([when,symbol,*prices,volume,None,'alpaca_1min'])
                if rows:
                    con.execute('BEGIN')
                    try:
                        write_rows(con,'mh.underlying_minutes',
                                   ['ts_utc','symbol','open','high','low','close','volume','oi','source'],rows)
                        con.execute('COMMIT')
                    except Exception:
                        con.execute('ROLLBACK');raise
                total+=len(rows)
                print(f'  {symbol} {day}: {len(rows)} underlying1-minute bars',flush=True)
            except Exception as exc:
                failures+=1
                print(f'  {symbol} {day}: underlying minute session failed ({type(exc).__name__})',flush=True)
            status('download_minute_bars_fill_underlyings',symbol=symbol,day=str(day),bars_total=total,failures=failures)
    return failures
