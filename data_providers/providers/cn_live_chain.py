"""Interactive Sina chain refresh with provider-owned immutable raw receipts."""
import re
from datetime import datetime, timezone
from .sina_futures import SinaFutures
from .cn_workspace import stored_chain
from ..jobs.history.maintain_cn_futures import quotes
from ..runtime import data_home


def chain(venue, product, month, live=False):
    if venue not in ('SHFE','DCE','CZCE','INE','CFFEX','GFEX') or not re.fullmatch(r'[A-Z]{1,4}',product):
        raise ValueError('Choose a catalog-listed CN venue and product')
    if not re.fullmatch(re.escape(product)+r'\d{3,4}', month):
        raise ValueError('Choose an exact listed futures month')
    stored = stored_chain(venue,product,month)
    if not stored:
        raise ValueError('Contract month not present in the provider catalog')
    warning, rows = None, stored
    if live:
        try:
            client = SinaFutures(data_home()/'var/interactive-quotes/cn/raw',tries=1,timeout=(5,12))
            param = product.lower() if venue=='CZCE' else product.lower()+'_o'
            received = datetime.now(timezone.utc)
            raw = quotes(client.chain(param,venue.lower(),month),venue,product,month,received)
            keys = ['source','venue','product','contract','underlying_contract','security_type','strike','call_put',
                    'ts_utc','last','bid','ask','oi','raw_json']
            rows = [{k:v for k,v in zip(keys,r) if k!='raw_json'} for r in raw]
            if not rows:
                raise ValueError('Source returned no chain rows')
        except Exception as exc:
            rows = stored
            warning = f'Live refresh failed ({type(exc).__name__}); showing stored observations.'
    return dict(rows=rows, warning=warning, requested_live=live, source='Sina / provider',
                mode='refreshed snapshot' if live and not warning else 'stored snapshot',
                timestamp_basis='provider receipt time; exchange quote timestamp unavailable',
                limitation='Not an exchange-certified real-time feed. Closed-market quotes may be unchanged.')
