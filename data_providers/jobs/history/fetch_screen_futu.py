import os
import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""fetch_screen_futu.py — the live IV/vol panel from Futu OpenD (free plan) → databases.

Per universe symbol, `get_option_underlying_overview` returns what no free US feed other
than this gives: iv, **iv_rank**, **iv_percentile**, hv30/60/90/120/365 with percentiles,
and **call/put volume + open interest**. Two destinations, no duplication:

  mh.iv_history       the IV itself (day, underlying, NULL, iv, spot, source='futu_panel')
  mh.option_flow_daily  everything else: call/put OI (an INPUT — it cannot be re-fetched
                      historically), vendor IV rank/percentile + HV (provenance we are
                      validated against), call/put volume, and the derived put/call ratios

Quota: OpenD 30 req/30s per category — sleeps accordingly. Idempotent per (day, symbol).

    uv run python apps/screener/fetch_screen_futu.py \
        [--tickers-file plans/screener_universe.txt]
"""
import argparse
import datetime as dt
import pathlib
import sys
import time

from data_providers.shared.net import throttle

import pandas as pd

from data_providers.data import clock  # noqa: E402
from data_providers.data import db  # noqa: E402

ROOT = bootstrap.ROOT
TODAY = clock.today()


def _has_mhro(con):
    """True when the merged file is reachable — it is best-effort, since a writer elsewhere
    can make the read-only attach fail, in which case mhro is only a view over staging."""
    try:
        con.execute("SELECT 1 FROM mhro.option_flow_daily LIMIT 1")
        return True
    except Exception:
        return False


def num(v):
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None



def pick(row, *keys):
    """Futu's panel column names have shifted between SDK versions — match by keyword."""
    for k in keys:
        for col in row.index:
            if str(col).lower().replace("_", "") == k.lower().replace("_", ""):
                return row[col]
    for k in keys:
        for col in row.index:
            if k.lower().replace("_", "") in str(col).lower().replace("_", ""):
                return row[col]
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers-file", default=str(ROOT / "plans" / "screener_universe.txt"))
    ap.add_argument("--market", default="US")
    ap.add_argument("--force", action="store_true", help="refresh panels already captured today")
    args = ap.parse_args()
    try:
        from data_providers.shared import files
        tickers = [t.upper() for t in files.safe_read_ticker_file(args.tickers_file)]
    except (FileNotFoundError, ValueError, OSError) as e:
        print(f"Error: {e}")
        return

    from futu import OpenQuoteContext
    ctx = OpenQuoteContext(host=os.environ.get("FUTU_HOST", "host.docker.internal" if pathlib.Path("/.dockerenv").exists() else "127.0.0.1"), port=11111)
    # STAGING, not history="rw": market_history is held read-write by nothing here, but a
    # staged job elsewhere attaches it read-only as `mhro`, and DuckDB refuses a read-write
    # open while any other handle exists. Writing to staging/ means this panel can run
    # concurrently with the IV sweep and the 1-min downloaders instead of queueing behind
    # them. Rows become visible on `merge_staging.py`.
    # universe=True attaches the yahoo warehouse as `yh`, which is where the spot price
    # now comes from (the futu panel does not return one -- see the note at the IV read).
    try:
        con = db.connect(log=None, staging="futu", snapshot=True, universe=True)
    except Exception:
        ctx.close()
        raise
    # Bare tickers belong to this equity market; CN futures are explicitly namespaced.
    tickers = [t for t in tickers if not t.startswith("CN:")]
    ok = 0
    # Do not re-download what we already hold today. Union staging with the merged file —
    # a staged pass that only looked at its own file would re-fetch every symbol the
    # previous pass had already merged, and pay 30 req/30s for the privilege.
    done_today = {r[0] for r in con.execute(
        """SELECT underlying FROM (
               SELECT underlying FROM mh.option_flow_daily
                WHERE source='futu_panel' AND day = CURRENT_DATE
               UNION
               SELECT underlying FROM mhro.option_flow_daily
                WHERE source='futu_panel' AND day = CURRENT_DATE)""").fetchall()} \
        if _has_mhro(con) else {r[0] for r in con.execute(
            """SELECT underlying FROM mh.option_flow_daily
               WHERE source='futu_panel' AND day = CURRENT_DATE""").fetchall()}
    if args.force: done_today.clear()
    skipped = [t for t in tickers if t in done_today]
    if skipped:
        print(f"  skipping {len(skipped)} symbols already fetched today: "
              f"{skipped[:8]}{'…' if len(skipped) > 8 else ''}")
    tickers = [t for t in tickers if t not in done_today]
    try:
        for tk in tickers:
            try:
                # Futu spells a class share with a DOT where Yahoo uses a HYPHEN: US.BRK.B,
                # not US.BRK-B. Passing the Yahoo form straight through made every class
                # share fail with "Unknown stock. BRK-B" (~dozens across the universe), and
                # the failure is silent in the sense that it looks like the vendor simply has
                # no options for the name. Same family as the OCC-root normalisation in
                # download_atm_iv.py: the STORED `underlying` keeps the Yahoo spelling for
                # warehouse joins — only the vendor code is translated.
                ret, df = ctx.get_option_underlying_overview(
                    [f"{args.market}.{tk.replace('-', '.')}"])
            except Exception as e:
                print(f"  {tk}: {repr(e)[:60]}"); throttle("futu"); continue
            if ret != 0 or df is None or df.empty:
                print(f"  {tk}: panel unavailable ({str(df)[:50]})"); throttle("futu"); continue
            r = df.iloc[0]
            if ok == 0:
                print("  panel columns:", list(df.columns))
            iv = num(pick(r, "iv"))
            # `spot` is NOT in the futu response -- get_option_underlying_overview returns
            # code/name/volumes/OI/iv/iv_rank/iv_percentile/pre_iv/hv_*, and nothing else.
            # The old `pick(r, "spot", "underlying_price")` could therefore only ever
            # return None, which is why every merged futu row had spot IS NULL and a note
            # reading "spot=None". Asking a vendor for a field it does not send is not a
            # lookup, it is a guaranteed miss -- so the price comes from our own warehouse,
            # which we already trust for the adjustment factors.
            spot = None
            try:
                _s = con.execute(
                    "SELECT close FROM yh.prices WHERE symbol=? ORDER BY date DESC LIMIT 1",
                    [tk]).fetchone()
                spot = float(_s[0]) if _s and _s[0] is not None else None
            except Exception:
                pass          # warehouse absent or thin: better a NULL than a wrong price
            # the panel reports PERCENT (21.4); the column convention is a FRACTION (0.214).
            # Mixing them silently corrupts IVR/VRP — caught by selfcheck 2026-10-01.
            if iv is not None:
                iv = iv / 100.0
            # Bound AFTER conversion, not just before. A one-sided `> 3` guard let junk
            # straight through: PDSB 7.86 and TVRD 11.06 were staged, and the merged table's
            # CHECK (0 < atm_iv <= 5) then aborted the entire 1,948-row batch. A sub-3
            # percent reading (the panel does return them — hv30 min 0.00238) is the mirror
            # hazard: 1.5% would be stored as IV 1.5 = 150%.
            if iv is not None and not (0 < iv <= 5):
                print(f"  {tk}: IV {iv} out of range after unit conversion — rejected")
                iv = None
            cvol, pvol = num(pick(r, "call_volume")), num(pick(r, "put_volume"))
            coi, poi = num(pick(r, "call_open_interest", "call_oi")), num(pick(r, "put_open_interest", "put_oi"))
            pcr_vol = (pvol / cvol) if (cvol and pvol) else None
            pcr_oi = (poi / coi) if (coi and poi) else None
            if iv:
                con.execute("INSERT OR REPLACE INTO mh.iv_history VALUES (?,?,NULL,?,?,?)",
                            [TODAY, tk, iv, spot, "futu_panel"])
            con.execute("""INSERT OR REPLACE INTO mh.option_flow_daily
                (day, underlying, source, call_oi, put_oi, iv_rank, iv_percentile, hv30, hv365,
                 call_vol, put_vol, pcr_vol, pcr_oi, method, note)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        [TODAY, tk, "futu_panel", coi, poi,
                         num(pick(r, "iv_rank")), num(pick(r, "iv_percentile")),
                         num(pick(r, "hv_30d", "hv30")) / 100 if num(pick(r, "hv_30d", "hv30")) else None,
                         num(pick(r, "hv_365d", "hv365")) / 100 if num(pick(r, "hv_365d", "hv365")) else None,
                         cvol, pvol, pcr_vol, pcr_oi,
                         "vendor panel (futu get_option_underlying_overview)",
                         f"spot={spot} iv={iv}"])
            ok += 1
            print(f"  {tk}: iv={iv} rank={num(r.get('iv_rank'))} "
                  f"pcr(vol)={None if pcr_vol is None else round(pcr_vol, 2)} "
                  f"pcr(oi)={None if pcr_oi is None else round(pcr_oi, 2)}", flush=True)
            throttle('futu')         # 30 req/30s per category, enforced centrally
    finally:
        ctx.close()
        con.close()
    print(f"panel rows stored: {ok}/{len(tickers)}")


if __name__ == "__main__":
    main()
