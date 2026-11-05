#!/usr/bin/env python3
"""Append the price-CSV tail from Nasdaq, for when yfinance is rate-limited.

Measured 2026-10-10: yfinance returned YFRateLimitError for a full day, freezing every price
CSV at 2026-10-05. That is not cosmetic -- apps/screener/download_atm_iv.py drops any bar
whose day has no spot close (`day not in px`), so the whole US IV series stopped advancing.

Overlap matches -> append; differs -> SKIP and report, because a corporate action restates
every earlier bar and patching would leave one file holding two adjustment regimes. Nasdaq
publishes no adjusted series, so a new day goes to BOTH files at factor 1.0. That is correct,
since the adjustment is anchored to the newest bar: only a dividend falling between the last
stored day and now would leave the older adj_close rows unscaled, and 35_refresh_tail.py
repairs that on its next successful yfinance run.

Writes data/raw + data/adjusted; rebuild with scripts/60_build_database.py.
"""
import argparse
import concurrent.futures as cf
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
from data_providers.runtime import ROOT
WS = ROOT / "market_data"
from data_providers.providers.nasdaq import daily_bars  # noqa: E402

if "--help" in sys.argv: print(__doc__); sys.exit(0)   # never start a download on --help

OVERLAP = 5        # stored bars re-checked against Nasdaq before anything is appended


def read_last(path):
    """({ISO date: close} over the last OVERLAP bars, newest date)."""
    try:
        lines = path.read_text().splitlines()[-OVERLAP:]
    except OSError:
        return {}, None
    # DDMMYYYY, not MMDDYYYY -- `23092026` is 23 Sep 2026 (month 23 does not exist).
    out = {f"{p[0][4:8]}-{p[0][2:4]}-{p[0][0:2]}": float(p[4]) for p in
           (l.split(",") for l in lines) if len(p) >= 6 and len(p[0]) == 8}
    return out, max(out, default=None)


def one(sym):
    """-> (symbol, message, appended). Message is non-empty for anything but a no-op."""
    raw = WS / "data" / "raw" / f"{sym}.csv"
    if not raw.exists():
        return sym, "FAILED: no csv", 0
    stored, newest = read_last(raw)
    have = daily_bars(sym)
    common = sorted(set(stored) & set(have))
    # UNKNOWN, not FAILED: a delisted symbol and an unreachable API are identical from here,
    # and the fetched count tells them apart. Settle it with Massive's whole-market grouped
    # feed -- ONE request for every symbol -- rather than by re-hammering Nasdaq. EQR, WBS and
    # LBRDK all report 0 bars because all three were delisted in mid-August 2026.
    if not common:
        return sym, f"UNKNOWN: nothing to verify against ({len(have)} bars fetched)", 0
    if any(abs(stored[d] - have[d][3]) >= 0.02 for d in common):
        return sym, "FAILED: OVERLAP DIFFERS -> needs a full refetch", 0
    new = sorted(d for d in have if d > newest)
    if not new:
        return sym, "", 0
    lines = []
    for d in new:
        o, h, l, c, v = have[d]
        y, m, dd = d.split("-")
        lines.append(f"{dd}{m}{y},{o:.4f},{h:.4f},{l:.4f},{c:.4f},{int(v)}")
    for path in (raw, WS / "data" / "adjusted" / f"{sym}.csv"):
        with open(path, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    return sym, f"+{len(new)} rows ({new[0]} .. {new[-1]})", len(new)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers-file", required=True)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    words = pathlib.Path(a.tickers_file).read_text().splitlines()
    tickers = [t.strip().upper() for t in words if t.strip() and not t.startswith("#")]
    print(f"{len(tickers)} tickers", flush=True)
    ok = fails = unknown = rows = 0
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for sym, msg, n in ex.map(one, tickers):
            ok, rows = ok + (n > 0), rows + n
            fails += "FAILED" in msg
            unknown += "UNKNOWN" in msg
            if msg or n:            # only a quiet no-op stays silent
                print(f"  {sym:<6} {msg or 'no new days'}", flush=True)
    print(f"updated {ok}, unchanged {len(tickers)-ok-fails-unknown}, UNKNOWN {unknown}, "
          f"FAILED {fails}, appended {rows:,}")


if __name__ == "__main__":
    main()
