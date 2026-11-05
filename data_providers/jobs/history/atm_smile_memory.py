"""Cached central strikes remain part of ATM selection during incremental fills."""
import math
import datetime as dt
from data_providers.jobs.history.option_root_history import contract, windows


def read_smile(con, source, underlying, expiry, start, end):
    rows = con.execute('''SELECT day,strike,call_put,iv,symbol FROM mhro.option_greeks_daily
        WHERE source=? AND underlying=? AND expiry=? AND day BETWEEN ? AND ?''',
        [source,underlying,expiry,start,end]).fetchall()
    result = {}
    for day,strike,cp,iv,symbol in rows:
        root = windows(underlying, day, day)[0][0]
        exp = dt.date.fromisoformat(str(expiry)[:10])
        if symbol != contract(underlying, root, exp, cp, strike):
            continue
        if iv is not None and math.isfinite(iv) and iv>0:
            result.setdefault(str(day)[:10],{})[(float(strike),cp)] = iv
    return result


def listed_strikes(quoted, cached):
    return sorted(set(quoted) | {k for k,_cp in cached})


def atm_mark(spot, fresh, cached):
    # Stored Greeks win a duplicate key, matching the writer's ON CONFLICT behavior.
    points = {key:iv for key,iv in {**fresh,**cached}.items()
              if iv is not None and math.isfinite(iv) and iv>0}
    if not points:
        return None
    strike = min(sorted({k for k,_cp in points}),key=lambda k:abs(k-spot))
    ivs = [iv for (k,_cp),iv in points.items() if k==strike]
    return strike,sum(ivs)/len(ivs)
