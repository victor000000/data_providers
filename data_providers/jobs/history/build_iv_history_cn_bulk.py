import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""build_iv_history_cn_bulk.py — daily ATM-IV series for CZCE products, one call per product-year.

The sibling `build_iv_history_cn.py` walks the CZCE EOD endpoint one TRADING DAY at a time
(260+ throttled scrapes per product) and only knows 甲醇 (MA) and 原油 (SC). CZCE also serves
a whole year in a single file — `option_hist_yearly_czce(symbol='SR', year='2026')` returns
~31k rows including the exchange's own DELTA and 隐含波动率 — so a full daily IV series for a
product costs ONE request, not 260.

That matters because cn.iv_history held series for only MA and SC while cn.option_bars held
bars for 11 products: nine products had chains and no IVR. Verified 2026-10-05, SR 2026 in
one call. The exchange IV is used as-is (percent -> fraction); a CNY futures option needs
futures carry rather than `r`, so our BS solver's IV/delta disagree with the venue's and the
venue's own number is the one to rank on (see the CN note in the data-sources memory).

Writes DIRECTLY to cn_market.duckdb. This is the "repoint the CN fetchers at cn_market" the
merge guard in data/staging/merge_staging.py asks for: CN must never be folded back into the
US-only market_history, and the akshare staging file's merge target is exactly that.

Idempotent: DELETE-then-INSERT per (day, underlying, source); re-running a year replaces it.

    uv run python apps/screener/build_iv_history_cn_bulk.py \
        [--products CF FG OI RM SA SR TA UR] [--years 2025 2026]
"""
import argparse
import datetime as dt
import pathlib
import sys

import duckdb
from data_providers.data.schema.cn_products import PRODUCTS  # noqa: E402

ROOT = bootstrap.ROOT
DB = ROOT / "cn_market.duckdb"
# Derived from the SCHEMA, not hand-listed: the literal 8-product list this replaces left the
# 5 CZCE products added later (MA, PK, ZC, PX, SH) frozen at 2026-09-30, and nothing said so.
DEFAULT_PRODUCTS = [p[2] for p in PRODUCTS if p[4] == "option_hist_yearly_czce"]
CORRECTED_SOURCE = "czce_native_parity_atm_v2"


def series_from_year(df):
    """(day, atm_iv) pairs: most-traded month, ATM strike by put-call parity, C/P IV averaged."""
    import numpy as np
    import pandas as pd

    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    # The year files are not one shape: 2020 names the contract column 品种代码, 2021+ 合约代码
    # (verified 2026-10-08). Both reads below go through 合约代码, so rename rather than alias.
    if "合约代码" not in df.columns:
        df = df.rename(columns={"品种代码": "合约代码"})
    if "合约代码" not in df.columns:
        # Neither name: a year the product had not listed (FG all years, OI/PK before 2022,
        # PX/SA/SH/UR before 2023) parses to an empty or unnamed frame. [] contributes nothing;
        # the KeyError this replaces aborted the whole sweep (call site is outside the try).
        return []
    code = df["合约代码"].astype(str)
    df = df[code.str.match(r"^[A-Za-z]+\d+[CP]\d+$")]
    if df.empty:
        return []
    code = df["合约代码"].astype(str)
    df["m"] = code.str.extract(r"^([A-Za-z]+\d+)[CP]")
    df["K"] = code.str.extract(r"[CP](\d+)$").astype(float)
    df["cp"] = code.str.extract(r"([CP])\d+$")
    vol_col = next((c for c in ("成交量(手)", "成交量", "成交量(张)") if c in df.columns), None)
    settle_col = next((c for c in ("今结算", "结算价") if c in df.columns), None)
    if vol_col is None or settle_col is None:
        raise KeyError(f"missing volume/settle column; have {list(df.columns)[:10]}")
    def numeric(column):
        return pd.to_numeric(df[column].astype(str).str.replace(",", "", regex=False).str.strip(),
                             errors="coerce")

    df["vol"] = numeric(vol_col)
    df["settle"] = numeric(settle_col)
    # "--" for a contract that did not trade; coerced to NaN rather than parsed as a number
    df["iv"] = numeric("隐含波动率") / 100.0
    df["day"] = pd.to_datetime(df["交易日期"].astype(str).str.strip()).dt.date

    out = []
    for day, g in df.groupby("day"):
        g = g.dropna(subset=["vol"])
        if g.empty:
            continue
        m = g.groupby("m")["vol"].sum().idxmax()
        gm = g[g["m"] == m]
        c = gm[gm["cp"] == "C"].set_index("K")
        p = gm[gm["cp"] == "P"].set_index("K")
        common = c.index.intersection(p.index)
        if len(common) == 0:
            continue
        # ATM by parity: |C_settle - P_settle| is smallest at the strike nearest the future.
        # Drop NaNs first: a strike that did not settle on the day (common early in a
        # contract's life) makes the whole difference series NA and idxmin raises rather
        # than returning the best of the strikes that did settle.
        diff = (c.loc[common, "settle"] - p.loc[common, "settle"]).abs().dropna()
        if diff.empty:
            continue
        K = diff.idxmin()
        iv = np.nanmean([c.loc[K, "iv"], p.loc[K, "iv"]])
        if not np.isfinite(iv) or not 0 < iv <= 5:
            continue
        out.append((day, float(iv)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--products", nargs="*", default=DEFAULT_PRODUCTS)
    ap.add_argument("--years", nargs="*", default=["2025", "2026"])
    args = ap.parse_args()

    from data_providers.providers.czce import yearly_options
    from data_providers.shared.net import retry, throttle

    con = duckdb.connect(str(DB))
    total = {}
    for code in args.products:
        # Legacy numeric parsing discarded comma-formatted values. Never append
        # corrected observations to those differently derived source histories.
        src = CORRECTED_SOURCE
        rows = []
        for year in args.years:
            def _once(code=code, year=year):
                throttle("akshare")
                return yearly_options(symbol=code, year=year)
            try:
                df = retry(_once, tries=3, what=f"yearly CZCE {code} {year}")
            except Exception as e:
                print(f"  {code} {year}: fetch failed {repr(e)[:70]}")
                continue
            got = series_from_year(df)
            rows.extend(got)
            print(f"  {code} {year}: {len(df):,} contract-days -> {len(got)} daily IV points")
        if not rows:
            print(f"{code}: nothing to write")
            continue
        con.executemany("DELETE FROM iv_history WHERE day=? AND underlying=? AND source=?",
                        [(d, code, src) for d, _ in rows])          # idempotent re-run
        con.execute("BEGIN")
        con.executemany("INSERT INTO iv_history VALUES (?, ?, NULL, ?, NULL, ?)",
                        [(d, code, iv, src) for d, iv in rows])
        con.execute("COMMIT")
        total[code] = len(rows)
    con.close()
    print("wrote:", total)


if __name__ == "__main__":
    main()
