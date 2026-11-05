"""contracts.py — read ONE option contract's daily history off Sina.

Split from chains.py (which finds the contracts) because this repo caps a file at 100 lines,
and these are the two readers the solver path and the bars path each use.
"""
import datetime as dt

from data_providers.shared.net import retry, throttle


def _day(v):
    return dt.date.fromisoformat(str(v)[:10])


def _sina_fn(ak, venue):
    return (ak.option_cffex_zz1000_daily_sina if venue == "CFFEX"
            else ak.option_commodity_hist_sina)


def contract_bars(ak, venue, code):
    """[(day, open, high, low, close, volume)] for one contract -- the OHLC the solver path
    throws away. Sina is the only source for DCE (HTTP 412) and CFFEX MO, so their bars come
    from here. No settle, OI or exchange IV: Sina does not carry them."""
    throttle("akshare")
    fn = (ak.option_cffex_zz1000_daily_sina if venue == "CFFEX"
          else ak.option_commodity_hist_sina)
    df = retry(lambda: fn(symbol=code), what=code)
    out = []
    if df is None or not len(df):
        return out
    for _, r in df.iterrows():
        vals = []
        for col in ("open", "high", "low", "close", "volume"):
            try:
                v = float(str(r[col]).replace(",", ""))
            except (KeyError, ValueError):
                v = None
            # NaN is TRUTHY, so `if close` is not a guard: a pandas NaN close would be stored
            # as a price (DuckDB sorts NaN above every number, and `close >= 0` passes it).
            vals.append(v if v is not None and v == v else None)
        if vals[3]:
            out.append((_day(r["date"]), *vals))
    return out


def contract_hist(ak, venue, code):
    """{day: (close, volume)} for ONE option contract, whole life in one request."""
    throttle("akshare")
    fn = (ak.option_cffex_zz1000_daily_sina if venue == "CFFEX"
          else ak.option_commodity_hist_sina)
    df = retry(lambda: fn(symbol=code), what=code)
    out = {}
    if df is None or not len(df):
        return out
    for _, r in df.iterrows():
        try:
            c = float(str(r["close"]).replace(",", ""))
            v = float(str(r["volume"]).replace(",", "") or 0)
        except ValueError:
            continue
        if c and c > 0:
            out[_day(r["date"])] = (c, v or 0.0)
    return out
