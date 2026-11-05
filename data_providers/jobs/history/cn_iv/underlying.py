"""underlying.py — the daily series for what a CN option is written ON.

Split out of build_cn_futures.py to stay under the file-size limit, and because the readers
are the part worth testing: every value that is not a number must come back as None rather
than as a price. CN frames carry empty strings and dashes for missing, and pandas turns those
into NaN -- which is TRUTHY, so `if close` would store it (DuckDB sorts NaN above every number
and `close >= 0` passes it).
"""
import datetime as dt

from data_providers.shared.net import retry

ZZ1000 = "sh000852"          # the index CFFEX MO options are written on


def num(v):
    try:
        f = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # NaN is not a price


def day(v):
    return dt.date.fromisoformat(str(v)[:10])


def main_series(ak, code):
    """[(day, o, h, l, c, settle, volume, oi)] for one product's continuous front month.

    Sina's `M0`-style series is the exchange's front-month ROLL, not a contract, so callers
    store it under the continuous symbol -- a consumer that needs a specific month must fetch
    that month. One request covers the product's whole published history.
    """
    df = retry(lambda: ak.futures_main_sina(symbol=f"{code}0"), what=f"{code}0")
    out = []
    for _, r in df.iterrows():
        close = num(r["收盘价"])
        if close is None:
            continue
        out.append((day(r["日期"]), num(r["开盘价"]), num(r["最高价"]), num(r["最低价"]),
                    close, num(r.get("动态结算价")), num(r.get("成交量")), num(r.get("持仓量"))))
    return out


def index_series(ak, symbol=ZZ1000):
    """Same shape, for a product whose underlying is an INDEX rather than a future."""
    df = retry(lambda: ak.stock_zh_index_daily(symbol=symbol), what=symbol)
    return [(day(r["date"]), None, None, None, num(r["close"]), None, num(r.get("volume")), None)
            for _, r in df.iterrows() if num(r["close"]) is not None]
