"""Latest ingested CN tracker rows, preserving account, snapshot and Greek provenance."""
import json
import math
import re
from .cn_workspace import connection, records, latest_futures
from .option_quote import number
from ..client import MarketData

VENUES = {'上期所':'SHFE','郑商所':'CZCE','大商所':'DCE','能源中心':'INE','中金所':'CFFEX','广期所':'GFEX'}


def signed(value):
    try:
        result = float(str(value).strip())
        return result if math.isfinite(result) else None
    except (TypeError,ValueError):
        return None


def positions():
    client = MarketData(private=True)
    release = client.release.id if client.release else None
    with connection('trades', release) as c:
        snapshots = records(c, '''SELECT kind,sha256,modified_utc,filename FROM broker_exports
            WHERE kind IN ('cn_tracker_positions','kq_positions')
            QUALIFY row_number() OVER(PARTITION BY kind ORDER BY modified_utc DESC)=1''')
        rows = []
        for receipt in snapshots:
            payloads = records(c, 'SELECT payload FROM broker_export_rows WHERE kind=? AND sha256=? ORDER BY row_number',
                               [receipt['kind'], receipt['sha256']])
            for item in payloads:
                p = json.loads(item['payload'])
                match = re.fullmatch(r'([A-Za-z]+)(\d{3,4})-?([CP])-?([\d.]+)',p.get('持仓合约','').strip())
                if not match:
                    continue
                account = p['用户代码'].strip()
                qty = signed(p.get('总仓'))
                if not qty or p.get('买卖','').strip() not in ('买','卖'):
                    continue
                qty *= -1 if p.get('买卖','').strip() == '卖' else 1
                rows.append(dict(account=account,broker='CN tracker',symbol=p['持仓合约'],venue=VENUES.get(p.get('交易所'),p.get('交易所')),
                    root=match[1].upper(), future=(match[1]+match[2]).upper(),right=match[3],strike=float(match[4]),
                    expiry=p.get('到期日'),qty=qty,entry=number(p.get('开仓均价')),mark=number(p.get('最新价')),
                    pnl_cny=signed(p.get('对价盈亏')),position_delta=signed(p.get('持仓Delta')),
                    position_gamma=signed(p.get('持仓Gamma')),position_theta=signed(p.get('持仓Theta')),
                    position_vega=signed(p.get('持仓Vega')),as_of=receipt['modified_utc'],receipt=receipt['sha256'],
                    source=receipt['kind'],timestamp_basis='export file modification UTC; market event time unavailable'))
    # Different source formats may represent the same account; use its newest complete tracker snapshot.
    latest = {a:max(r['as_of'] for r in rows if r['account']==a) for a in {r['account'] for r in rows}}
    rows = [r for r in rows if r['as_of']==latest[r['account']]]
    with connection('reference', release) as c:
        specs = records(c, 'SELECT product,multiplier,source,updated FROM futures_products')
    spots = latest_futures(sorted({r['future'] for r in rows}),release)
    for r in rows:
        spec = next((s for s in specs if s['product'].upper()==r['root']), {})
        spot = next((s for s in spots if s['contract'].upper()==r['future'] and s['venue']==r['venue']), {})
        r.update(multiplier=spec.get('multiplier'),multiplier_source=spec.get('source'),
                 underlying_price=spot.get('close'),underlying_as_of=spot.get('day'),underlying_source=spot.get('source'))
    return dict(positions=rows, release=release, snapshots=[{k:v for k,v in s.items() if k!='filename'} for s in snapshots],
                limitation='Ingested broker-export snapshots, not a live CN account connection; missing Greek units remain unknown.')
