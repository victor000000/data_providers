"""chains.py — the raw material for the solver path: Sina chains, one request per contract.

Sina serves the venues whose own endpoints will not: DCE answers HTTP 412 behind a JavaScript
challenge, CFFEX MO carries no IV column, and GFEX refuses after ~20 requests.
"""
import datetime as dt

from data_providers.jobs.history.cn_iv import fetch
from data_providers.shared.net import retry, throttle

ZZ1000 = "sh000852"          # the index CFFEX MO options are written on


def _day(v):
    return dt.date.fromisoformat(str(v)[:10])


def underlying_path(ak, venue, code):
    """{day: close} for the thing the option is written on, in ONE request per product."""
    if venue == "CFFEX":
        df = retry(lambda: ak.stock_zh_index_daily(symbol=ZZ1000), what=ZZ1000)
        return {_day(r["date"]): float(r["close"]) for _, r in df.iterrows()}
    df = retry(lambda: ak.futures_main_sina(symbol=f"{code.upper()}0"), what=f"{code}0")
    out = {}
    for _, r in df.iterrows():
        try:
            c = float(str(r["收盘价"]).replace(",", ""))
        except ValueError:
            continue
        if c:
            out[_day(r["日期"])] = c
    return out


def synthetic_grid(step, path, lo, hi, ymd):
    """Strikes on a fixed grid around the forward, for a product Sina's page does not list.

    Sina serves 聚丙烯 contracts by code (pp2611C8500 returns 38 rows) but omits the product
    from the option page chain_table scrapes, so there is no ladder to read. The grid is the
    exchange's, verified 2026-10-07: on pp2611, 8500/8600/8700/8800 exist and 8550/8650 do
    not -- 100 CNY apart.

    Deliberately NOT a fallback for a month whose table call failed: an unlisted month
    (DCE lists 8 of 12 for 豆粕) fails the same way, and synthesizing for it is how a third of
    a run once got spent asking for contracts that do not exist. Only a product known to be
    absent from the page takes this path, and it takes it for every month.
    """
    ks = sorted({round(path[d] / step) * step for d in path if lo <= d <= hi})
    if not ks:
        return []
    out, k = [], ks[0] - 3 * step
    while k <= ks[-1] + 3 * step:
        out.append((float(k), f"{ymd}C{k:.0f}", f"{ymd}P{k:.0f}"))
        k += step
    return out


def month_grid(ak, venue, name, ymd, step, path, lo, hi):
    """One month's ladder: hand-built when `step` is given, else the venue's own table."""
    if step:
        return synthetic_grid(step, path, lo, hi, ymd)
    return chain_table(ak, venue, name, ymd)


def sina_name(venue, name):
    """cn_product_map carries each venue's OWN endpoint names, and Sina's differ for GFEX:
    the map says '工业硅' (the variety) where Sina wants '工业硅期权'."""
    return name + "期权" if venue == "GFEX" and not name.endswith("期权") else name


def chain_table(ak, venue, name, ymd):
    """[(strike, call_code, put_code)] for one month -- the ladder and the contract names.

    A month this product does not list raises fetch.Unlisted: a normal answer (DCE lists
    1,3,5,7,8,9,11,12 for 豆粕), NOT a glitch to work around, and NOT the same thing as a
    request that went unanswered -- see fetch.table, which keeps those apart."""
    throttle("akshare")
    if venue == "CFFEX":
        df = fetch.table(lambda: ak.option_cffex_zz1000_spot_sina(symbol=ymd), f"{ymd} table")
        ccol, pcol = "看涨合约-标识", "看跌合约-标识"
    else:
        df = fetch.table(lambda: ak.option_commodity_contract_table_sina(
            symbol=sina_name(venue, name), contract=ymd), f"{ymd} table")
        ccol, pcol = "看涨合约-看涨期权合约", "看跌合约-看跌期权合约"
    if "行权价" not in df.columns or ccol not in df.columns:
        return []
    out = []
    for _, r in df.iterrows():
        try:
            k = float(str(r["行权价"]).replace(",", ""))
        except ValueError:
            continue
        c, p = r.get(ccol), r.get(pcol)
        if k and isinstance(c, str) and isinstance(p, str) and c and p:
            out.append((k, c.strip(), p.strip()))
    return sorted(out)
