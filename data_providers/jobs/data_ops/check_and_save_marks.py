import os
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

"""check_and_save_marks.py — pull every source, CHECK CONSISTENCY, then save.

THE ONE MARK CAPTURE. Two narrower scripts were retired into this one on
2026-10-05 (recoverable from git history):

  tws_marks.py   TWS-only ticks/chains, plus an accountSummary capture. The marks
                 half was a strict subset of this file; the account half wrote the
                 SAME account_daily rows, under the SAME source label
                 'ibkr_tws_snapshot', with the same DELETE-then-INSERT idempotency,
                 as apps/data_ops/fetch_tws_account.py. So it duplicated two things.
  live_marks.py  yfinance + Nasdaq only. Also a strict subset.

Neither could fail safely in the other's absence either: this file already flags
any single source as unavailable and carries on, so a "just TWS today" run is this
script with the other sources erroring, not a second implementation of the same
table writes.

The rule this enforces: a number is only written to the log of record when it survived a
cross-check, and every row carries the source that produced it. Saving first and checking
later is how a bad mark becomes a permanent "fact".

Sources, and what each is actually worth:
  TWS (ib_async)      live underlying quotes + option previous close. The only genuinely
                      independent broker mark we have.
  Yahoo (yfinance)    option last/OI/volume. NOTE: Yahoo and Nasdaq agree to the penny on
                      every leg — they are ONE tape, not two. Counting them as independent
                      confirmation is false confidence, so they are collapsed into a single
                      "consensus" source here.
  Nasdaq (HTTP)       same tape as Yahoo; used only as a reachability fallback.
  akshare (CZCE)      CN options WITH exchange DELTA and 隐含波动率 — the exchange daily
                      file itself. Cross-validated against our own BS solver below.
  Sina                CN underlying last price.

Checks performed:
  1. underlying: TWS vs Yahoo, flag > 0.5% disagreement
  2. legs:       TWS last vs consensus last, flag > 5% relative (or > $0.05 absolute on
                 cheap options, where a cent is noise)
  3. CN legs:    our bs.implied_vol() from the settlement price vs the exchange's own
                 隐含波动率 — this validates the whole BS pipeline against the exchange.

    uv run --no-project --with duckdb --with ib_async --with yfinance --with akshare \
        python scripts/check_and_save_marks.py
"""
import datetime as dt
import pathlib
import sys
import urllib.request

import akshare as ak

from data_providers.providers import tws
import duckdb
import yfinance as yf
from ib_async import IB, Option, Stock

from data_providers.data import clock  # noqa: E402
from data_providers.compute import bs  # noqa: E402
from data_providers.data import db  # noqa: E402

ROOT = bootstrap.ROOT
EXPIRY = dt.date(2026, 11, 20)
CN_EXPIRY = dt.date(2026, 12, 11)
TODAY = clock.today()
from data_providers.shared.rates import R_US, R_CN

US_LEGS = [("META", 630.0, "P"), ("META", 650.0, "P"), ("META", 850.0, "C"),
           ("CRWD", 220.0, "P"), ("CRWD", 230.0, "P"), ("CRWD", 330.0, "C"),
           ("SWKS", 75.0, "P"), ("SWKS", 110.0, "C"),
           ("TLT", 74.0, "P"), ("TLT", 82.0, "C"),
           ("EWZ", 32.0, "P"), ("EWZ", 43.0, "C")]
US_SYMS = ["META", "CRWD", "SWKS", "TLT", "EWZ"]
CN_LEGS = [("MA701", "MA701P2650", 2650.0, "P"), ("MA701", "MA701C3350", 3350.0, "C")]

flags = []


def flag(msg):
    flags.append(msg)
    print(f"   !! {msg}")


def num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def g(t, *names):
    for n in names:
        v = getattr(t, n, None)
        if callable(v):
            v = v()
        if v is not None and v == v:
            v = float(v)
            if v > 0:
                return v
    return None


def main():
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    con = duckdb.connect(str(ROOT / "trades.duckdb"))
    ticks, snaps = [], []

    # ---------------- 1. underlyings: TWS vs Yahoo ----------------
    ib = IB()
    ib.RequestTimeout = 45
    tws.connect(ib, client_id=95, timeout=25)
    cs = ib.qualifyContracts(*[Stock(s, "SMART", "USD") for s in US_SYMS])
    tws_spot = {}
    for s, t in zip(US_SYMS, ib.reqTickers(*cs)):
        v = g(t, "last", "close")
        if v:
            tws_spot[s] = v

    # Futu: free plan gives US *underlying* snapshots and the IV/IV-rank panel, but NOT
    # option chains ("No permission ... US MarketOptions"). Used here as a third
    # independent underlying mark, and for the IV context saved further down.
    futu_spot, futu_iv = {}, {}
    try:
        from futu import OpenQuoteContext
        ctx = OpenQuoteContext(host=os.environ.get("FUTU_HOST", "host.docker.internal" if pathlib.Path("/.dockerenv").exists() else "127.0.0.1"), port=11111)
        ret, d = ctx.get_market_snapshot([f"US.{s}" for s in US_SYMS])
        if ret == 0:
            for _, r in d.iterrows():
                futu_spot[r["code"].split(".")[1]] = float(r["last_price"])
        ret2, d2 = ctx.get_option_underlying_overview([f"US.{s}" for s in US_SYMS])
        if ret2 == 0:
            for _, r in d2.iterrows():
                futu_iv[r["code"].split(".")[1]] = {
                    "iv": num(r.get("iv")), "iv_rank": num(r.get("iv_rank")),
                    "iv_pct": num(r.get("iv_percentile")), "hv30": num(r.get("hv_30d")),
                    "call_oi": num(r.get("call_open_interest")), "put_oi": num(r.get("put_open_interest")),
                    "call_vol": num(r.get("call_volume")), "put_vol": num(r.get("put_volume"))}
        ctx.close()
    except Exception as e:
        flag(f"futu unavailable: {type(e).__name__} {e}")

    # Alpaca via raw REST (alpaca-py is not installed and the box cannot reach PyPI — the
    # existing downloaders use urllib + the key in secrets/, so this does too).
    alp_spot, alp_leg = {}, {}
    try:
        import json
        sec = json.loads((ROOT / "secrets" / "alpaca.json").read_text())
        hdr = {"APCA-API-KEY-ID": sec["key_id"], "APCA-API-SECRET-KEY": sec["secret"]}
        u = ("https://data.alpaca.markets/v2/stocks/snapshots?symbols=" + ",".join(US_SYMS)
             + "&feed=iex")
        snap = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=hdr), timeout=30))
        for s, v in (snap or {}).items():
            q = (v or {}).get("latestQuote") or {}
            if q.get("bp"):
                alp_spot[s] = (float(q["bp"]) + float(q["ap"])) / 2
        u2 = ("https://data.alpaca.markets/v1beta1/options/snapshots/META"
              f"?expiration_date={EXPIRY.isoformat()}&feed=indicative&limit=1000")
        osnap = json.load(urllib.request.urlopen(urllib.request.Request(u2, headers=hdr), timeout=30))
        for sym, v in (osnap.get("snapshots") or {}).items():
            # OCC symbol: META261120C00850000
            try:
                body = sym[len("META"):]
                exp, cp, strike = body[:6], body[6], int(body[7:]) / 1000.0
                if exp == EXPIRY.strftime("%y%m%d"):
                    alp_leg[(strike, cp)] = {"iv": v.get("impliedVolatility"),
                                             "delta": (v.get("greeks") or {}).get("delta"),
                                             "quote": v.get("latestQuote")}
            except Exception:
                pass
        print(f"   [alpaca] {len(alp_spot)} underlying snapshots, "
              f"{len(alp_leg)} META Nov-20 contracts with greeks")
    except Exception as e:
        flag(f"alpaca unavailable: {type(e).__name__} {str(e)[:120]}")

    print("=== CHECK 1: underlying — TWS vs Yahoo vs Futu vs Alpaca ===")
    spot = {}
    for s in US_SYMS:
        try:
            y = float(yf.Ticker(s).fast_info["lastPrice"])
        except Exception:
            y = None
        # CRITICAL: these sources do NOT all report the same instant. Yahoo and Futu both
        # return the previous SESSION CLOSE (they agreed to the cent on every name), while
        # TWS and Alpaca return the LIVE pre-market quote. Comparing across the two groups
        # measures the overnight gap, not a data error — so compare within each group.
        a, fu, al = tws_spot.get(s), futu_spot.get(s), alp_spot.get(s)
        live = [v for v in (a, al) if v]          # TWS, Alpaca
        close = [v for v in (y, fu) if v]         # Yahoo, Futu
        for label, vals in (("live ", live), ("close", close)):
            if len(vals) >= 2:
                spread = (max(vals) - min(vals)) / (sum(vals) / len(vals)) * 100
                ok = spread <= 0.5
                print(f"   {s:5s} {label} spread {spread:5.2f}%  "
                      f"[{' '.join(f'{v:.2f}' for v in vals)}]  {'ok' if ok else 'DISAGREE'}")
                if not ok:
                    flag(f"{s} {label}: {' vs '.join(f'{v:.2f}' for v in vals)} = {spread:.2f}% apart")
        if live and close:
            gap = (live[0] - close[0]) / close[0] * 100
            print(f"   {s:5s} overnight gap (live {live[0]:.2f} vs close {close[0]:.2f}): {gap:+.2f}%")
        # the live mark is what a trade would face
        spot[s] = a or al or y or fu

    # ---------------- 2. US legs: TWS vs Yahoo/Nasdaq consensus ----------------
    print("\n=== CHECK 2: US legs — TWS last vs consensus last ===")
    opt_cs = ib.qualifyContracts(*[Option(s, EXPIRY.strftime("%Y%m%d"), k, cp, "SMART",
                                          currency="USD", multiplier="100")
                                   for s, k, cp in US_LEGS])
    tws_leg = {}
    for (s, k, cp), t in zip(US_LEGS, ib.reqTickers(*opt_cs)):
        tws_leg[(s, k, cp)] = g(t, "last", "close")
    ib.disconnect()

    cons_leg, meta = {}, {}
    for s in US_SYMS:
        try:
            ch = yf.Ticker(s).option_chain(EXPIRY.isoformat())
            for cp, df in (("C", ch.calls), ("P", ch.puts)):
                for _, r in df.iterrows():
                    cons_leg[(s, float(r["strike"]), cp)] = num(r["lastPrice"])
                    meta[(s, float(r["strike"]), cp)] = (num(r["openInterest"]), num(r["volume"]))
        except Exception as e:
            flag(f"yahoo chain {s} failed: {e}")

    for s, k, cp in US_LEGS:
        a, b = tws_leg.get((s, k, cp)), cons_leg.get((s, k, cp))
        if not a or not b:
            flag(f"{s} {int(k)}{cp}: missing mark (tws={a} cons={b})")
            continue
        rel = abs(a - b) / max(a, b) * 100
        ok = rel <= 5 or abs(a - b) <= 0.05
        print(f"   {s:5s}{k:>7.0f}{cp}  TWS {a:7.2f}  cons {b:7.2f}  rel {rel:5.1f}%  {'ok' if ok else 'DISAGREE'}")
        if not ok:
            flag(f"{s} {int(k)}{cp}: TWS {a} vs consensus {b} = {rel:.1f}% apart")

    # ---------------- 3. CN legs: our BS IV vs the exchange's own IV ----------------
    print("\n=== CHECK 3: CN legs — bs.implied_vol vs CZCE 隐含波动率 ===")
    req = urllib.request.Request("https://hq.sinajs.cn/list=nf_MA2701",
                                 headers={"Referer": "https://finance.sina.com.cn"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk")
    f = raw.split('"')[1].split(",")
    ma_spot = num(f[8])          # project convention for nf_: last price at index 8
    print(f"   MA2701 (Sina nf_MA2701 idx8) = {ma_spot}")
    ticks.append((now, "MA2701", ma_spot, "sina", f"raw open {f[0]} high {f[1]} low {f[2]} prev_settle {f[7]}"))

    cz = ak.option_hist_czce(symbol="甲醇期权", trade_date="20260930")
    cz["合约代码"] = cz["合约代码"].astype(str)
    T_cn = bs.T_years(CN_EXPIRY, TODAY)
    cn_rows = []
    for sym, code, K, cp in CN_LEGS:
        r = cz[cz["合约代码"] == code]
        if not len(r):
            flag(f"{code}: not in the CZCE file")
            continue
        r = r.iloc[0]
        settle, exch_iv, exch_delta = num(r["今结算"]), num(r["隐含波动率"]), num(r["DELTA"])
        ours_iv = bs.implied_vol(settle, ma_spot, K, T_cn, R_CN, cp == "C")
        ours_d = bs.bs_delta(ma_spot, K, T_cn, R_CN, ours_iv, cp == "C") if ours_iv else None
        if ours_iv and exch_iv:
            iv_rel = abs(ours_iv * 100 - exch_iv) / exch_iv * 100
            print(f"   {code}  settle {settle:7.2f}  exch IV {exch_iv:5.2f}%  ours {ours_iv*100:5.2f}%  "
                  f"rel {iv_rel:4.1f}%   exch Δ {exch_delta:+.4f}  ours {ours_d:+.4f}")
            if iv_rel > 15:
                flag(f"{code}: our IV {ours_iv*100:.2f}% vs exchange {exch_iv:.2f}% = {iv_rel:.1f}% apart")
            if exch_delta and ours_d and abs(exch_delta - ours_d) > 0.06:
                flag(f"{code}: our delta {ours_d:+.4f} vs exchange {exch_delta:+.4f}")
        cn_rows.append((sym, code, K, cp, settle, exch_iv, exch_delta, ma_spot,
                        num(r["持仓量"]), num(r["成交量(手)"])))

    # ---------------- 4. save what passed ----------------
    print("\n=== SAVING ===")
    if ticks:   # DuckDB raises on an empty list, not a no-op
        con.executemany("INSERT INTO underlying_ticks (ts_utc, symbol, price, source, note) "
                        "VALUES (?,?,?,?,?)", ticks)
    for s in US_SYMS:
        sid = con.execute("INSERT INTO chain_snapshots (ts_utc, underlying, expiry, spot, source) "
                          "VALUES (?,?,?,?,?) RETURNING snap_id",
                          [now, s, EXPIRY, spot.get(s), "multi_source_checked"]).fetchone()[0]
        snaps.append(sid)
        rows = []
        for sym, k, cp in US_LEGS:
            if sym != s:
                continue
            tws_v, cons_v = tws_leg.get((s, k, cp)), cons_leg.get((s, k, cp))
            oi, vol = meta.get((s, k, cp), (None, None))
            # store the consensus mark; keep TWS in `mid` only when they agree, else NULL
            mark = cons_v if (tws_v and cons_v and abs(tws_v - cons_v) / max(tws_v, cons_v) <= 0.05) else cons_v
            iv = bs.implied_vol(mark, spot.get(s), k, bs.T_years(EXPIRY, TODAY), R_US, cp == "C") \
                if (mark and spot.get(s)) else None
            rows.append([sid, s, EXPIRY, k, cp, None, None, mark, iv, None, oi, spot.get(s)])
        if rows:   # DuckDB raises on an empty list, not a no-op
            con.executemany("""INSERT INTO chain_rows
                (snap_id, underlying, expiry, strike, call_put, bid, ask, mid, iv, delta, oi, spot)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    if cn_rows:
        sid = con.execute("INSERT INTO chain_snapshots (ts_utc, underlying, expiry, spot, source) "
                          "VALUES (?,?,?,?,?) RETURNING snap_id",
                          [now, "MA701", CN_EXPIRY, ma_spot, "czce_daily_file_20260930"]).fetchone()[0]
        snaps.append(sid)
        con.executemany("""INSERT INTO chain_rows
            (snap_id, underlying, expiry, strike, call_put, bid, ask, mid, iv, delta, oi, spot)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [[sid, s, CN_EXPIRY, k, cp, None, None, settle, exch_iv / 100.0, exch_delta, oi, spot_]
             for s, code, k, cp, settle, exch_iv, exch_delta, spot_, oi, vol in cn_rows])
    # Futu's IV panel -> iv_history (the IV itself) + option_flow_daily (everything else).
    # This is the only free source with a vendor IV rank/percentile to validate ours against.
    # iv_history lives in market_history.duckdb, which a downloader may hold the write lock
    # on. Best effort: never let a lock here lose the marks already written above.
    panel_n = 0
    try:
        con.execute(f"ATTACH '{ROOT / 'market_history.duckdb'}' AS mh")
        for s, v in futu_iv.items():
            if v.get("iv"):
                # Futu serves IV in PERCENT (48.0 = 48%); iv_history.atm_iv is a FRACTION
                # and carries CHECK (atm_iv <= 5), so an unconverted write is rejected. It
                # was: the constraint caught it rather than storing 48.0 as a 4800% IV.
                con.execute("INSERT OR REPLACE INTO mh.iv_history (day, underlying, expiry, atm_iv, spot, source) "
                            "VALUES (?,?,?,?,?,?)",
                            [TODAY, s, None, v["iv"] / 100.0, spot.get(s), "futu_panel_check"])
                panel_n += 1
    except Exception as e:
        flag(f"futu IV panel not saved (market_history locked?): {type(e).__name__}")
    con.close()

    print(f"   {len(ticks)} underlying ticks · {len(snaps)} chain snapshots (snap_id {snaps}) · "
          f"{panel_n} futu IV panel rows")
    print("\n=== FUTU IV PANEL (context: the book is short vega) ===")
    for s, v in sorted(futu_iv.items(), key=lambda kv: -(kv[1].get("iv_pct") or 0)):
        print(f"   {s:5s} IV {v['iv']:5.1f}  rank {v['iv_rank']:5.1f}  pct {v['iv_pct']:5.1f}  "
              f"HV30 {v['hv30'] if v['hv30'] else float('nan'):5.1f}  "
              f"callOI {v['call_oi'] or 0:9.0f}  putOI {v['put_oi'] or 0:9.0f}")
    print(f"\n{'CONSISTENCY: ' + str(len(flags)) + ' flag(s)' if flags else 'CONSISTENCY: all checks passed'}")
    for f_ in flags:
        print(f"   - {f_}")


if __name__ == "__main__":
    main()
