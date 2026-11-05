import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""build_option_flow.py — put/call ratio, volume and a gamma-exposure proxy, from stored bars.

History for the flow layer, computed only from inputs we already hold (`option_bars`
volume + bar-implied IV + the warehouse's spot). No downloads.

  call_vol / put_vol   summed contract volume per underlying-day
  pcr_vol              put_vol / call_vol   (the classic put/call ratio)
  gex_proxy            Σ [ Γ(contract) × volume × 100 × S² × 0.01 ] with calls +, puts −
                       (the dealer-short convention). **Honest limits**: weighted by VOLUME
                       where open interest is unavailable, and only over the tracked strike
                       grid (9 anchors per expiry), so it is a proxy for direction and
                       regime, not a dollar figure. Once option_flow_daily has been captured
                       daily from the live panel, OI-weighted GEX supersedes it for those days.

Idempotent per (underlying, day): rows are replaced, never appended twice.

    uv run python apps/screener/build_option_flow.py [--symbols ...]
"""
import argparse
import datetime as dt
import pathlib
import sys

from data_providers.compute import bs  # noqa: E402
from data_providers.data import db  # noqa: E402

ROOT = bootstrap.ROOT
from data_providers.shared.rates import R_US as R
from data_providers.jobs.history.option_spots import historical_close


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--since", type=dt.date.fromisoformat, default=dt.date(1900, 1, 1))
    args = ap.parse_args()

    con = db.connect(log=None, history="rw", universe=True)
    syms = args.symbols or [r[0] for r in con.execute(
        "SELECT DISTINCT underlying FROM mh.option_bars ORDER BY 1").fetchall()]

    for sym in syms:
        splits = con.execute("SELECT date,ratio FROM yh.splits WHERE symbol=?",[sym]).fetchall()
        bars = con.execute("""
            SELECT b.day, b.expiry, b.strike, b.call_put, b.close, b.volume, p.close
            FROM mh.option_bars b
            LEFT JOIN yh.prices p ON p.symbol = b.underlying AND p.date = b.day
            WHERE b.underlying=? AND b.day>=? AND b.close > 0 AND isfinite(b.close)
            QUALIFY row_number() OVER(PARTITION BY b.symbol,b.day ORDER BY
                CASE b.source WHEN 'alpaca' THEN 0 WHEN 'thetadata_eod' THEN 1 ELSE 2 END,
                b.source)=1""", [sym, args.since]).fetchall()
        if not bars:
            print(f"{sym}: no bars"); continue
        # any source will do for the fallback (CN products have no warehouse row) —
        # one close per day, the freshest source wins by insertion order
        spot_fallback = dict(con.execute(
            """SELECT day, any_value(close) FROM mh.underlying_daily WHERE symbol=?
               GROUP BY day""", [sym]).fetchall())
        agg = {}
        for day, expiry, K, cp, close, vol, spot in bars:
            S = historical_close(day,spot,splits) if spot else spot_fallback.get(day)
            if not S:
                continue
            a = agg.setdefault(day, {"cv": 0.0, "pv": 0.0, "gex": 0.0})
            if cp == "C":
                a["cv"] += vol or 0
            else:
                a["pv"] += vol or 0
            T = max((expiry - day).days, 1) / 365.0
            iv = bs.implied_vol(close, S, K, T, R, cp == "C")
            if iv and 0.03 < iv < 3.0:
                gamma = bs.bs_greeks(S, K, T, R, iv, cp == "C")[1]
                sign = 1.0 if cp == "C" else -1.0
                a["gex"] += sign * gamma * (vol or 0) * 100 * S * S * 0.01
        rows = []
        for day, a in sorted(agg.items()):
            pcr = (a["pv"] / a["cv"]) if a["cv"] else None
            rows.append([day, sym, "computed_from_bars", None, None, None, None, None, None,
                         a["cv"], a["pv"], pcr, None, a["gex"],
                         "volume-weighted, stored strike coverage",
                         "OI not available historically; PCR from volume"])
        # Bail out BEFORE the delete when there is nothing to write.
        #
        # `DELETE` then `executemany` is not atomic here -- DuckDB autocommits each
        # statement -- so a symbol that aggregates to zero rows deleted its history and
        # then raised "executemany requires a non-empty list of parameter sets", leaving
        # nothing behind. A full-universe run hit this on MA and destroyed 268 flow-days.
        # "I computed nothing" is not a reason to delete what is on disk; it means this
        # run has no opinion about this symbol, so the previous rows stand.
        if not rows:
            print(f"{sym}: no usable bars this run — leaving existing flow rows untouched")
            continue
        con.execute("BEGIN")
        con.executemany("""INSERT OR REPLACE INTO mh.option_flow_daily
            (day, underlying, source, call_oi, put_oi, iv_rank, iv_percentile, hv30, hv365,
             call_vol, put_vol, pcr_vol, pcr_oi, gex_proxy, method, note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        con.execute("COMMIT")
        print(f"{sym}: {len(rows)} flow-days "
              f"{rows[0][0]} → {rows[-1][0]}" if rows else f"{sym}: nothing")
    con.close()


if __name__ == "__main__":
    main()
