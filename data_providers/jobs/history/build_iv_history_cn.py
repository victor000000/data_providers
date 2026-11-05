import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""build_iv_history_cn.py — retroactive daily ATM-IV series for CN commodity options.

For each trading day it reads the exchange EOD file (akshare: CZCE 甲醇期权 / INE 原油期权),
picks the **most-traded contract** of the day, locates the ATM strike via put-call parity
(|C_settle - P_settle| minimized) and stores the average of that strike's C/P exchange IVs
into `iv_history` (source akshare_czce_hist / akshare_ine_hist). Non-trading days are skipped.

This is the IVR/IVP input series for the China book (screener_21dte reads iv_history).

Usage: uv run python build_iv_history_cn.py [--days 260]
"""
import argparse
import datetime as dt
import pathlib
import sys
import re

import socket

socket.setdefaulttimeout(30)   # akshare/OpenD calls carry no timeout of their own —
# a hung request used to hold the DB lock until someone noticed per-day exchange scrape

import akshare as ak
from data_providers.data import db  # scripts/db.py — knows the three paths
from data_providers.compute.bs import implied_vol, T_years
from data_providers.shared.net import throttle

DB = "trades.duckdb"


def one_day(con, day, label, fn):
    try:
        df = fn()
    except Exception:
        return False
    if df is None or len(df) == 0 or "合约代码" not in df.columns:
        return False
    df = df.copy()
    if "隐含波动率" in df.columns:
        df["iv"] = df["隐含波动率"].astype(str)
        df = df[df["iv"] != "--"]
    else:  # INE/SHFE: no IV column -> compute from settle via parity spot + BS (approximate T)
        df["iv"] = None
    if df.empty:
        return False
    # group by underlying-month (letters+YYMM before C/P)
    df["m"] = df["合约代码"].astype(str).str.extract(r"^([A-Za-z]+\d+)[CP]")
    df["K"] = df["合约代码"].astype(str).str.extract(r"(?i)[CP](\d+)$").astype(float)
    # CZCE names the column 成交量(手); SHFE (INE crude) just 成交量 — the same
    # venue-convention trap that broke the CN option-bar download (selfcheck 2026-10-01).
    vol_col = next((c for c in ("成交量(手)", "成交量", "成交量(张)") if c in df.columns), None)
    if vol_col is None:
        raise KeyError(f"no volume column; have {list(df.columns)[:8]}")
    df["vol"] = df[vol_col].astype(float)
    pick = None
    for m, g in df.groupby("m"):
        if g.empty:
            continue
        vol = g["vol"].sum()
        calls = g[g["合约代码"].str.contains("C")]
        puts = g[g["合约代码"].str.contains("P")]
        if calls.empty or puts.empty:
            continue
        merged = calls.merge(puts, on="K", suffixes=("_c", "_p"))
        if merged.empty:
            continue
        settle_col = "今结算" if "今结算" in df.columns else "结算价"
        merged["diff"] = (merged[settle_col + "_c"].astype(float) - merged[settle_col + "_p"].astype(float)).abs()
        row = merged.loc[merged["diff"].idxmin()]
        if "iv" in df.columns and row.get("iv_c") is not None and str(row["iv_c"]) != "None":
            try:
                iv = (float(row["iv_c"]) + float(row["iv_p"])) / 2 / 100.0
            except (TypeError, ValueError):
                iv = None
        else:
            iv = None
        if iv is None:  # BS from settle + parity spot; T approximated per product rule
            import datetime as _dt
            K = float(row["K"])
            C = float(row[settle_col + "_c"]); P = float(row[settle_col + "_p"])
            S = K + C - P
            mdel = re.match(r"^[A-Za-z]+(\d{2})(\d{2})$", str(m))
            yy, mm = int(mdel.group(1)), int(mdel.group(2))
            exp = _dt.date(2000 + yy, mm, 12) - _dt.timedelta(days=30)  # delivery month - 1, approx
            import datetime as _d
            T = max((exp - _d.date(int(day[:4]), int(day[4:6]), int(day[6:8]))).days / 365.0, 0.01)
            ivc = implied_vol(C, S, K, T, 0.04, True); ivp = implied_vol(P, S, K, T, 0.04, False)
            if ivc and ivp:
                iv = (ivc + ivp) / 2
        cand = (vol, m, iv, float(row["K"]))
        if pick is None or cand[0] > pick[0]:
            pick = cand
    if not pick:
        return False
    _, m, iv, K = pick
    iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    con.execute("DELETE FROM mh.iv_history WHERE day=? AND underlying=? AND source=?",
                [iso, label, f"akshare_{label.lower()}_hist"])
    con.execute("INSERT INTO mh.iv_history VALUES (?, ?, NULL, ?, NULL, ?)",
                [iso, label, iv, f"akshare_{label.lower()}_hist"])
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=260)
    ap.add_argument("--only", default=None, choices=["MA", "SC"])
    args = ap.parse_args()
    con = db.connect(log=None, staging="akshare")
    end = dt.date(2026, 9, 30)
    got = {"MA": 0, "SC": 0}
    d = end
    todo = 0
    while (end - d).days <= args.days:
        if d.weekday() < 5:
            todo += 1
        d -= dt.timedelta(days=1)
    print(f"scanning {todo} weekdays back to {d}")
    d = end
    n = 0
    while (end - d).days <= args.days:
        if d.weekday() < 5:
            iso = d.strftime("%Y%m%d")
            if args.only in (None, "MA"):
                try:
                    throttle('akshare')   # per-day exchange scrape — the biggest loop we run
                    if one_day(con, iso, "MA", lambda: ak.option_hist_czce(symbol="甲醇期权", trade_date=iso)):
                        got["MA"] += 1
                except Exception as e:
                    print(f"  {iso} MA skip: {repr(e)[:80]}")
            if args.only in (None, "SC"):
                try:
                    if one_day(con, iso, "SC", lambda: ak.option_hist_shfe(symbol="原油期权", trade_date=iso)):
                        got["SC"] += 1
                except Exception as e:
                    print(f"  {iso} SC skip: {repr(e)[:80]}")
            n += 1
            if n % 10 == 0:
                print(f"  ...{d} MA:{got['MA']} SC:{got['SC']}")
        d -= dt.timedelta(days=1)
    print("done:", got)
    con.close()


if __name__ == "__main__":
    main()
