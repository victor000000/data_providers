import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

# ── --help guard ─────────────────────────────────────────────────────────────
# This script WRITES to a database and takes no arguments, so `script.py --help`
# used to execute it in full. Not hypothetical: a 2026-10-05 sweep ran every app
# with --help and rewrote the fills table (via seed_fills.py) and risk_profile.
# A read-only-looking flag must not be a write. Exits before any connection.
from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap below, and a
# docstring must be the first statement, so all 33 copies printed "(no docstring)".

"""fetch_cn_options.py — China option EOD data into trades.duckdb.

MA (CZCE): full MA701 chain with exchange DELTA + IV (akshare option_hist_czce), and
           enriches MA fills with that day's exchange IV/delta.
SC (INE):  full sc2612 chain via ak.option_hist_shfe '原油期权' (结算价/德尔塔); IV computed
           via bs.implied_vol from settle.
Both:      EOD daily OHLC (Sina) -> underlying_daily; snapshot spot = that day's settle
           (fallback: latest tick). Fetch-before-delete + retries: a failed fetch never
           wipes existing rows.

Usage: uv run python fetch_cn_options.py [YYYYMMDD ...]
"""
import datetime as dt
import pathlib
import re
import sys
import time

import socket

socket.setdefaulttimeout(30)   # akshare/OpenD calls carry no timeout of their own —
# a hung request used to hold the DB lock until someone noticed per-day CN chains

import akshare as ak
import duckdb

from data_providers.shared.net import throttle

from data_providers.compute.bs import implied_vol, T_years

DB = bootstrap.ROOT / "trades.duckdb"
from data_providers.shared.rates import R_US as R
def main():
    con = duckdb.connect(str(DB))
    days = sys.argv[1:] or ["20260928", "20260929"]
    MA_EXP = dt.date(2026, 12, 11)
    SC_EXP = dt.date(2026, 11, 12)


    def cn_ts(day):
        d = dt.date(int(day[:4]), int(day[4:6]), int(day[6:8]))
        return dt.datetime.combine(d, dt.time(15, 0)) - dt.timedelta(hours=8)  # 15:00 CST -> UTC


    def last_tick(symbol):
        r = con.execute("SELECT price FROM underlying_ticks WHERE symbol=? ORDER BY ts_utc DESC LIMIT 1", [symbol]).fetchone()
        return r[0] if r else None


    def daily_bar(symbol, day):
        """Store the day's EOD bar (Sina daily) into underlying_daily; return settle (fallback close)."""
        df = None
        for attempt in range(3):
            try:
                df = ak.futures_zh_daily_sina(symbol=symbol)
                break
            except Exception as e:
                print(f"  daily {symbol} fetch try{attempt + 1}: {repr(e)[:100]}")
                time.sleep(4)
        if df is None:
            return None
        d = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        row = df[df["date"].astype(str) == d]
        if row.empty:
            return None
        r = row.iloc[0]
        try:
            settle = float(r["settle"])
        except (TypeError, ValueError):
            settle = None
        con.execute("DELETE FROM underlying_daily WHERE day=? AND symbol=? AND source='sina_daily'", [d, symbol])
        con.execute("INSERT INTO underlying_daily VALUES (?,?,?,?,?,?,?,?,?, 'sina_daily')",
                    [d, symbol, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                     settle, float(r["volume"]), float(r["hold"])])
        print(f"  daily {symbol} {d}: O{r['open']} H{r['high']} L{r['low']} C{r['close']} settle={settle}")
        return settle if settle else float(r["close"])


    def delete_snaps(iso_day, source):
        con.execute("""DELETE FROM chain_rows WHERE snap_id IN (SELECT snap_id FROM chain_snapshots
        WHERE CAST(ts_utc AS DATE) = CAST(? AS DATE) AND source = ?)""", [iso_day, source])
        con.execute("DELETE FROM chain_snapshots WHERE CAST(ts_utc AS DATE) = CAST(? AS DATE) AND source = ?",
                    [iso_day, source])


    def fetch_retry(fn, label):
        for attempt in range(3):
            try:
                df = fn()
                if df is not None and len(df):
                    return df
                print(f"{label} try{attempt + 1}: empty")
            except Exception as e:
                print(f"{label} try{attempt + 1}: {repr(e)[:130]}")
            time.sleep(5)
        return None


    for day in days:
        iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"

        # ---------- MA (CZCE) ----------
        throttle('akshare')
        df = fetch_retry(lambda: ak.option_hist_czce(symbol="甲醇期权", trade_date=day), f"{day} MA")
        if df is not None and "合约代码" in df.columns:
            ma = df[df["合约代码"].astype(str).str.startswith("MA701")]
            spot = daily_bar("MA2701", day) or last_tick("nf_MA2701")
            delete_snaps(iso, "akshare_czce")
            snap = con.execute("""INSERT INTO chain_snapshots (ts_utc, underlying, expiry, spot, source)
            VALUES (?,?,?,?, 'akshare_czce') RETURNING snap_id""",
                [cn_ts(day), "MA701", MA_EXP, spot]).fetchone()[0]
            n = 0
            for _, r in ma.iterrows():
                m = re.match(r"MA701([CP])(\d+)$", str(r["合约代码"]))
                if not m:
                    continue
                cp, K = m.group(1), float(m.group(2))
                iv = r["隐含波动率"]
                iv = None if str(iv) in ("--", "nan", "None") or float(iv or 0) == 0 else float(iv) / 100.0
                con.execute("INSERT INTO chain_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [snap, "MA701", MA_EXP, K, cp, None, None, None, None,
                             float(r["今结算"]), iv, float(r["DELTA"]), None, None, None,
                             float(r["持仓量"]), float(r["成交量(手)"]), spot])
                n += 1
            # enrich MA fills with exchange IV/delta of that day
            for fill_id, sym_desc in con.execute(
                    "SELECT fill_id, symbol FROM fills WHERE symbol LIKE 'MA701%' AND date(ts_utc)=CAST(? AS DATE)",
                    [iso]).fetchall():
                m = re.search(r"MA701([CP])(\d+)", sym_desc or "")
                if not m:
                    continue
                row = ma[ma["合约代码"] == f"MA701{m.group(1)}{m.group(2)}"]
                if not row.empty:
                    r0 = row.iloc[0]
                    iv0 = None if str(r0["隐含波动率"]) == "--" else float(r0["隐含波动率"]) / 100.0
                    con.execute("UPDATE fills SET iv_at_fill=?, delta_at_fill=? WHERE fill_id=?",
                                [iv0, float(r0["DELTA"]), fill_id])
            print(f"{day} MA701: {n} contracts stored (snapshot #{snap}, spot={spot})")
        else:
            print(f"{day} MA err: no data — existing rows kept")

        # ---------- SC (INE) ----------
        df = fetch_retry(lambda: ak.option_hist_shfe(symbol="原油期权", trade_date=day), f"{day} SC")
        if df is not None and "合约代码" in df.columns:
            sc12 = df[df["合约代码"].astype(str).str.lower().str.startswith("sc2612")]
            sc_spot = daily_bar("SC2612", day) or last_tick("nf_SC2612")
            delete_snaps(iso, "akshare_ine")
            snap = con.execute("""INSERT INTO chain_snapshots (ts_utc, underlying, expiry, spot, source)
            VALUES (?,?,?,?, 'akshare_ine') RETURNING snap_id""",
                [cn_ts(day), "SC2612", SC_EXP, sc_spot]).fetchone()[0]
            n = 0
            for _, r in sc12.iterrows():
                m = re.match(r"sc2612([CP])(\d+)$", str(r["合约代码"]).strip(), re.I)
                if not m:
                    continue
                cp, K = m.group(1).upper(), float(m.group(2))
                try:
                    settle = float(r["结算价"])
                except (TypeError, ValueError):
                    settle = None
                try:
                    delta = float(r["德尔塔"])
                    if cp == "P" and delta > 0:
                        delta = -delta
                except (TypeError, ValueError):
                    delta = None
                iv = implied_vol(settle, sc_spot, K, T_years(SC_EXP), R, cp == "C") if (settle and sc_spot) else None
                try:
                    oi = float(r["持仓量"])
                    vol = float(r["成交量"])
                except (TypeError, ValueError):
                    oi = vol = None
                con.execute("INSERT INTO chain_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [snap, "SC2612", SC_EXP, K, cp, None, None, None, None,
                             settle, iv, delta, None, None, None, oi, vol, sc_spot])
                n += 1
            print(f"{day} SC2612: {n} contracts stored (snapshot #{snap}, spot={sc_spot})")
        else:
            print(f"{day} SC err: no data — existing rows kept")

    con.close()
    print("done")


# Guard added by the data/application split: unguarded, importing this module ran it.
if __name__ == "__main__":
    main()
