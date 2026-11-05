"""frames.py — one exchange day-frame in, one ATM IV out.

The two shapes the CN venues serve, and the rule each one needs:

  SHFE/INE  `option_vol_shfe` -> one row per 合约系列 with 成交量 and the exchange's own
            隐含波动率. No strikes, so the CZCE parity-ATM rule has nothing to select on and
            volume is the only honest way to pick which series the day's IV belongs to.
  GFEX      `option_hist_gfex` -> one row per CONTRACT (si2611-C-10000) with 成交量, 结算价
            and 隐含波动率, so the CZCE rule applies verbatim: most-traded month, ATM strike
            by put-call parity, C/P exchange IVs averaged.

Percent or fraction is NOT a guess: SHFE reports fractions (cu2611 = 0.204635) and GFEX
reports percents (31.46) -- verified 2026-10-07. One `if iv > 3` heuristic would misread a
400% print as a fraction, so each venue converts in its own function.
"""


def num(v):
    """CN frames use empty strings and '--' for a missing number, never a bare null."""
    if v is None:
        return None
    t = str(v).strip().replace(",", "")
    if not t or t in ("-", "--"):
        return None
    try:
        f = float(t)
    except ValueError:
        return None
    # NaN fails every comparison, so `f != f` is the test -- and NaN would otherwise be
    # stored (DuckDB sorts it above every number) rather than read as "no value".
    return f if f == f and abs(f) != float("inf") else None


def day_from_series_vol(df):
    """SHFE/INE: the most-traded 合约系列 of the day, its published 隐含波动率 (a fraction).

    The series can switch month between days; the CZCE builder's most-traded-month rule has
    the same property, so the two stay comparable.
    """
    vol_col = next((c for c in df.columns if "成交量" in c), None)
    iv_col = next((c for c in df.columns if "隐含波动率" in c), None)
    if not (vol_col and iv_col):
        return None
    rows = []
    for _, r in df.iterrows():
        v, iv = num(r[vol_col]), num(r[iv_col])
        if v is not None and iv is not None and 0 < iv <= 5:
            rows.append((v, iv))
    if not rows:
        return None
    return max(rows, key=lambda x: x[0])[1]


def day_from_contract_hist(df):
    """GFEX: most-traded month, ATM strike by parity, C/P exchange IVs averaged (percent in).

    |C_settle - P_settle| is smallest at the strike nearest the future, which avoids needing
    the underlying future at all -- and the frame carries no futures price to have used.
    """
    import numpy as np
    import pandas as pd

    code_col = next((c for c in df.columns if "合约" in c and "名称" in c), None)
    vol_col = next((c for c in df.columns if "成交量" in c), None)
    stl_col = next((c for c in df.columns if "结算价" in c and "前" not in c), None)
    iv_col = next((c for c in df.columns if "隐含波动率" in c), None)
    if not (code_col and vol_col and stl_col and iv_col):
        return None
    d = pd.DataFrame({
        "code": df[code_col].astype(str).str.strip(),
        "vol": pd.to_numeric(df[vol_col], errors="coerce"),
        "settle": pd.to_numeric(df[stl_col], errors="coerce"),
        "iv": pd.to_numeric(df[iv_col], errors="coerce") / 100.0,
    })
    parts = d["code"].str.extract(r"^([A-Za-z]+\d+)[-\s]?([CP])[-\s]?(\d+(?:\.\d+)?)$")
    d["m"], d["cp"], d["K"] = parts[0], parts[1], pd.to_numeric(parts[2], errors="coerce")
    d = d.dropna(subset=["m", "cp", "K", "vol"])
    if d.empty:
        return None
    gm = d[d["m"] == d.groupby("m")["vol"].sum().idxmax()]
    c = gm[gm["cp"] == "C"].set_index("K")
    p = gm[gm["cp"] == "P"].set_index("K")
    common = c.index.intersection(p.index)
    if len(common) == 0:
        return None
    diff = (c.loc[common, "settle"] - p.loc[common, "settle"]).abs().dropna()
    if diff.empty:
        return None
    K = diff.idxmin()
    iv = np.nanmean([c.loc[K, "iv"], p.loc[K, "iv"]])
    return float(iv) if np.isfinite(iv) and 0 < iv <= 5 else None
