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

"""load_qsqh_orders.py — 快期 OrderReport exports -> `orders` table.

Stores EVERY order event (filled, cancelled, modified-as-new-order) with its full
lifecycle fields. Idempotent per source file. Accepts multiple files/globs.

Usage: uv run python load_qsqh_orders.py <OrderReport.csv ...>
Default (no args): every OrderReport*.csv under var/raw/broker_dumps/.
"""
import csv
import datetime as dt
import glob
import io
import pathlib
import re
import sys

import duckdb

ROOT = bootstrap.ROOT
from data_providers.jobs.trades import source_dirs  # noqa: E402
DB = ROOT / "trades.duckdb"
ACTION = {"买": "BUY", "卖": "SELL"}
OC = {"开仓": "OPEN", "平昨": "CLOSE", "平今": "CLOSE"}


def parse_file(con, path):
    raw = pathlib.Path(path).read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.DictReader(io.StringIO(text)))
    con.execute("DELETE FROM orders WHERE source=?", [pathlib.Path(path).name])
    n = 0
    for r in rows:
        sym = (r.get("合约编码") or "").strip()
        m = re.match(r"^([A-Za-z]+)(\d{3,4})([CP])(\d+(?:\.\d+)?)$", sym)
        underlying = (m.group(1).upper() + m.group(2)) if m else sym
        try:
            ts = dt.datetime.strptime(f"{r['日期'].strip()} {r['时间'].strip()}", "%Y/%m/%d %H:%M:%S")
            ts_utc = ts - dt.timedelta(hours=8)
        except (KeyError, ValueError):
            ts_utc = None
        def fnum(key):
            try:
                return float(str(r.get(key, "")).strip())
            except ValueError:
                return None
        con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(r.get("委托书号") or "").strip(),
                     (r.get("帐号") or "").split(" ")[0] or None,
                     "浙商期货",
                     underlying, sym,
                     ACTION.get((r.get("买/卖") or "").strip(), (r.get("买/卖") or "").strip()),
                     OC.get((r.get("开平") or "").strip(), (r.get("开平") or "").strip()),
                     (r.get("类别") or "").strip(), (r.get("有效期") or "").strip(),
                     fnum("价格"), fnum("报单手数"), fnum("成交"), fnum("均价"),
                     (r.get("状态") or "").strip(), ts_utc,
                     (r.get("交易所") or "").strip(), "CNY",
                     (r.get("备注") or "").strip() or None,
                     pathlib.Path(path).name])
        n += 1
    print(f"{pathlib.Path(path).name}: {n} order events loaded")


def main():
    files = sys.argv[1:] or source_dirs.find("OrderReport*.csv")
    if not files:
        print("no OrderReport files found")
        return
    con = duckdb.connect(str(DB))
    for f in files:
        parse_file(con, f)
    print("orders total:", con.execute("SELECT count(*) FROM orders").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
