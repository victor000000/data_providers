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

"""cn_ticks.py — snapshot China-related prices into underlying_ticks (Sina feed).

Defaults: SC2612 crude · MA2701 methanol · WTI · Brent · US nat gas · PP2701 (olefins).
Run around China sessions (day open/close, night open) to build the price series.
  uv run python cn_ticks.py [code ...]
"""
import datetime as dt
import pathlib
import re
import sys
import urllib.request

import duckdb

from data_providers.shared.net import retry

DB = bootstrap.ROOT / "trades.duckdb"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
DEFAULT = ["nf_SC2612", "nf_MA2701", "nf_PP2701", "hf_CL", "hf_OIL", "hf_RB", "hf_HO", "hf_NG"]


def fetch(codes):
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"})
    # hq.sinajs.cn drops ~1/3 of connections (SSL: UNEXPECTED_EOF; 4 of 12 calls, 2026-10-12) and
    # akshare wraps this same host, so the fix is here: one attempt cost a leg its live spot.
    raw = retry(lambda: urllib.request.urlopen(req, timeout=15).read(), what=f"sina {','.join(codes)}")
    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    out = {}
    for line in text.strip().splitlines():
        m = re.match(r'var hq_str_(\w+)="([^"]*)"', line.strip())
        if not m or not m.group(2):
            continue
        f = m.group(2).split(",")
        if m.group(1).startswith("hf_"):
            price = float(f[0]) if f[0] else None          # global futures: last first
        else:
            price = None
            for idx in (8, 6, 2):
                try:
                    v = float(f[idx])
                    if v > 0:
                        price = v
                        break
                except (IndexError, ValueError):
                    continue
        if price:
            out[m.group(1)] = price
    return out


def main():
    codes = sys.argv[1:] or DEFAULT
    prices = fetch(codes)
    con = duckdb.connect(str(DB))
    # NAIVE UTC, deliberately. `datetime.now(timezone.utc)` is AWARE, and DuckDB
    # renders an aware datetime into a naive TIMESTAMP column using the SESSION
    # timezone -- which on this box is Asia/Shanghai even inside a process that has
    # set TZ=UTC, because bootstrap's pin does not reach DuckDB's binding. Every
    # tick was stored 8 hours ahead of true UTC (round-trip: 12:00Z read back as 20:00).
    ts = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for code, px in prices.items():
        con.execute("INSERT INTO underlying_ticks VALUES (?,?,?, 'sina', 'cn_ticks')", [ts, code, px])

    # SC driver rule: always record the international energy complex + SC-vs-Brent basis
    FX_USDCNY = 7.13
    sc, br = prices.get("nf_SC2612"), prices.get("hf_OIL")
    if sc and br:
        basis = sc / FX_USDCNY - br
        con.execute("INSERT INTO underlying_ticks VALUES (?,?,?, 'derived', 'SC_CNY/7.13 - Brent_USD')",
                    [ts, "basis_SC_brent_usd", round(basis, 3)])
        print(f"SC-Brent basis: ${basis:+.2f}/bbl  (SC {sc:.0f} CNY vs Brent {br:.2f})")
    con.close()
    print(f"{ts.strftime('%Y-%m-%d %H:%M')} UTC ticks stored:", prices)


# Guard, not decoration: unguarded, importing this module fetched Sina quotes and INSERTed
# them into the log of record. See the restructure plan.
if __name__ == "__main__":
    main()
