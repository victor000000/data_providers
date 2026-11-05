"""Read-only CN workspace views from the provider's pinned published release."""
from contextlib import contextmanager
from datetime import datetime, timezone
from ..client import MarketData
from ..lake import connect_store
from ..runtime import data_home


@contextmanager
def connection(store, release=None):
    c = connect_store(data_home(), store, release=release)
    try:
        yield c
    finally:
        c.close()


def records(c, sql, params=None):
    cur = c.execute(sql, params or [])
    keys = [r[0] for r in cur.description]
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def catalog():
    client = MarketData(private=True)
    release = client.release.id if client.release else None
    with connection('cn', release) as c:
        products = records(c, '''SELECT venue,product,underlying_contract,max(ts_utc) AS observed_at,
             count(DISTINCT contract) AS contracts FROM option_quotes GROUP BY ALL ORDER BY venue,product,underlying_contract''')
        iv = records(c, '''SELECT underlying,source,day,atm_iv FROM iv_history WHERE atm_iv>0 AND isfinite(atm_iv)
             QUALIFY row_number() OVER (PARTITION BY underlying,source ORDER BY day DESC)<=252 ORDER BY underlying,source,day''')
    status = []
    for dataset in ('cn_option_quotes','cn_daily','cn_iv','cn_option_history_checks'):
        try:
            status.append(client.status(dataset))
        except Exception as exc:
            status.append(dict(dataset=dataset,error=type(exc).__name__))
    return dict(products=products, iv_history=iv, status=status, release=release,
                received_at=datetime.now(timezone.utc).isoformat())


def stored_chain(venue, product, month):
    with connection('cn') as c:
        return records(c, '''SELECT source,venue,product,contract,underlying_contract,strike,call_put,
             ts_utc,last,bid,ask,oi FROM option_quotes WHERE venue=? AND product=? AND upper(underlying_contract)=?
             QUALIFY row_number() OVER(PARTITION BY contract ORDER BY ts_utc DESC)=1 ORDER BY strike,call_put LIMIT 1000''',
                       [venue, product, month])


def latest_futures(contracts, release=None):
    if not contracts:
        return []
    with connection('cn', release) as c:
        return records(c, '''SELECT venue,product,contract,day,close,volume,oi,source FROM futures_daily
             WHERE upper(contract) IN (SELECT unnest(?))
             QUALIFY row_number() OVER(PARTITION BY venue,contract ORDER BY day DESC)=1''', [contracts])
