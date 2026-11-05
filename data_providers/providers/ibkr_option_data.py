"""Async, bounded quote requests on a caller-owned IB connection; no order actions."""
import asyncio
from datetime import datetime, timezone
from ib_async import Stock, Option
from .option_quote import contract, quote, number


async def expiries(ib, underlying):
    stocks = await asyncio.wait_for(ib.qualifyContractsAsync(Stock(underlying, 'SMART', 'USD')), 12)
    if not stocks:
        raise ValueError('Underlying could not be qualified')
    rows = await asyncio.wait_for(ib.reqSecDefOptParamsAsync(underlying, '', 'STK', stocks[0].conId), 15)
    rows = [r for r in rows if r.exchange == 'SMART' and r.multiplier == '100' and r.tradingClass == underlying]
    return dict(expiries=sorted({f'{e[:4]}-{e[4:6]}-{e[6:]}' for r in rows for e in r.expirations}),
                strikes=sorted({s for r in rows for s in r.strikes}), partial=False,
                scope='IBKR standard SMART option parameters; individual contracts require qualification')


async def snapshots(ib, symbols, data_type=1):
    if not 1 <= len(symbols) <= 20:
        raise ValueError('IBKR quote requests are bounded to 20 contracts')
    specs = [contract(s) for s in symbols]
    options = [Option(s['underlying'], s['expiry'].replace('-',''), s['strike'], s['right'], 'SMART',
                      currency='USD', multiplier='100') for s in specs]
    qualified = await asyncio.wait_for(ib.qualifyContractsAsync(*options), 15)
    by_symbol = {c.localSymbol.replace(' ',''):c for c in qualified}
    tickers, errors = {}, []
    def on_error(req_id, code, message, c=None):
        if c and c.conId in {v.conId for v in qualified}:
            errors.append(dict(code=code, message=message))
    ib.errorEvent += on_error
    ib.reqMarketDataType(data_type)
    try:
        for symbol, c in by_symbol.items():
            tickers[symbol] = ib.reqMktData(c, '', False, False)
            tickers[symbol].marketDataType = 0  # SDK defaults to 1 before any broker callback.
        for _ in range(16):
            await asyncio.sleep(0.5)
            if tickers and all(number(t.bid) is not None and number(t.ask) not in (None, 0) and t.marketDataType in (1,2,3,4) for t in tickers.values()):
                break
            if errors:
                break
        received = datetime.now(timezone.utc).isoformat()
        rows = []
        for spec in specs:
            t = tickers.get(spec['symbol'])
            feed = {1:'live',2:'frozen',3:'delayed',4:'delayed_frozen'}.get(t.marketDataType,'unknown') if t else 'missing'
            q = quote(spec['symbol'], getattr(t,'bid',None), getattr(t,'ask',None), getattr(t,'bidSize',None),
                      getattr(t,'askSize',None), source='IBKR', feed=feed, received_at=received,
                      timestamp_basis='TWS tick receipt; exchange quote timestamp unavailable',
                      tick_received_at=t.time.isoformat() if t and t.time else None)
            rows.append(dict(**spec, quote=q, contract_status='qualified' if spec['symbol'] in by_symbol else 'unqualified'))
        return dict(rows=rows, source='IBKR', feed='per-contract', errors=errors,
                    warning='Exchange quote time is unavailable from these TWS ticks; receipt time is not quote age. '+ '; '.join(dict.fromkeys(e['message'] for e in errors)), next_page_token=None)
    finally:
        for symbol,c in by_symbol.items():
            if symbol in tickers:
                ib.cancelMktData(c)
        ib.errorEvent -= on_error


async def chain(ib, underlying, expiry, right, low, high, data_type=1):
    details = await expiries(ib, underlying)
    if expiry not in details['expiries']:
        raise ValueError('Expiry not listed by IBKR')
    if low is None or high is None:
        raise ValueError('Enter a strike range for IBKR (up to 20 strikes per refresh).')
    strikes = [s for s in details['strikes'] if low <= s <= high]
    symbols = [f"{underlying}{expiry.replace('-','')[2:]}{right}{round(s*1000):08d}" for s in strikes[:20]]
    result = await snapshots(ib, symbols, data_type) if symbols else dict(rows=[],source='IBKR',next_page_token=None)
    result['rows'] = [r for r in result['rows'] if r.get('contract_status')=='qualified']
    result['partial'] = len(strikes)>20
    return result


async def underlying(ib, symbol):
    qualified = await asyncio.wait_for(ib.qualifyContractsAsync(Stock(symbol, 'SMART', 'USD')), 10)
    if not qualified:
        raise ValueError('Underlying not qualified')
    c = qualified[0]
    ib.reqMarketDataType(1)
    ticker = ib.reqMktData(c, '', False, False)
    ticker.marketDataType = 0
    try:
        await asyncio.sleep(2)
        from .option_quote import number
        return dict(symbol=symbol, price=number(ticker.last), event_time=None, source='IBKR',
                    feed={1:'live',2:'frozen',3:'delayed',4:'delayed frozen'}.get(ticker.marketDataType,'unknown'),
                    price_kind='last trade; event clock unavailable', bars=[], adjustment='raw',
                    received_at=datetime.now(timezone.utc).isoformat())
    finally:
        ib.cancelMktData(c)
