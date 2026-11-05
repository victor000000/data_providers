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

"""load_cn_settlements.py — 浙商期货 daily settlement statements (结算单) -> trades.duckdb.

Parses each 结算单_YYYYMMDD.txt (GBK) and:
  * writes `account_daily` (settlement-truth: equity/margin/available/risk/fees/premiums)
  * corrects `fills` fees + margin_occupied to settlement values (9/28+ closable)
  * adds missing MO2610 opening fills from the 9/23 持仓明细 (open date/price known, time not)
  * fills realized_pnl on the 9/28 MO closes (economics vs open price)

Usage: uv run python load_cn_settlements.py <结算单*.txt ...>
Default: every 结算单_*.txt under var/raw/broker_dumps/2026-09-30_qsqh/settlements/.
"""
import datetime as dt
import glob
import pathlib
import re
import sys

import duckdb

ROOT = bootstrap.ROOT
from data_providers.jobs.trades import source_dirs  # noqa: E402
DB = ROOT / "trades.duckdb"
ACCT = "20101589"


def field(text, label, flags=re.S):
    m = re.search(label + r"[^：:]*[：:]\s*(-?[\d,]+\.?\d*)", text, flags)
    return float(m.group(1).replace(",", "")) if m else None


def parse_file(path):
    raw = pathlib.Path(path).read_bytes()
    text = raw.decode("gb18030", "replace")
    day = re.search(r"日期 Date：(\d{8})", text).group(1)
    day_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    head = text.split("成交记录")[0] if "成交记录" in text else text.split("持仓明细")[0]
    funds = {
        "bf": field(head, "期初结存"), "cf": field(head, r"期末结存"),
        "equity": field(head, "客户权益"), "margin": field(head, "保证金占用 Margin"),
        "avail": field(head, "可用资金"), "risk": field(head, r"风\s*险\s*度"),
        "realized": field(head, "平仓盈亏"), "mtm": field(head, "持仓盯市盈亏"),
        "fee": field(head, r"手\s*续\s*费"), "prem_in": field(head, "权利金收入"),
        "prem_out": field(head, "权利金支出"), "mv_short": field(head, r"空头期权市值"),
        "mv_equity": field(head, r"市值权益"),
    }
    trades, positions = [], []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        f = [c.strip() for c in line.strip("|").split("|")]
        if len(f) < 17:
            continue
        if re.match(r"^\d{8}$", f[0]) and f[6] in ("买", "卖"):          # 成交记录
            trades.append({"date": f[0], "exch": f[2], "product": f[4], "instrument": f[5],
                           "bs": f[6], "price": float(f[8]), "lots": float(f[9]),
                           "oc": f[11], "fee": float(f[12]), "realized": float(f[13])})
        elif f[0] == ACCT and len(f) > 6 and f[5].isdigit() and len(f[5]) == 8:   # 持仓明细
            positions.append({"open_date": f[5], "product": f[3], "instrument": f[4],
                              "bs": f[7], "lots": float(f[8]), "open_px": float(f[9]),
                              "margin": float(f[14])})
    return day_iso, funds, trades, positions


def main():
    files = sys.argv[1:] or source_dirs.find("结算单_*.txt")
    con = duckdb.connect(str(DB))
    for path in files:
        day_iso, funds, trades, positions = parse_file(path)
        con.execute("DELETE FROM account_daily WHERE account=? AND day=CAST(? AS DATE)", [ACCT, day_iso])
        con.execute("""INSERT INTO account_daily
            (day, account, currency, net_liq, cash, margin_used, available, realized_pnl,
             unrealized_pnl, fees, risk_ratio, source, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [day_iso, ACCT, "CNY", funds["equity"], None, funds["margin"], funds["avail"],
                     funds["realized"], funds["mtm"], funds["fee"], (funds["risk"] or 0) / 100.0,
                     f"settlement_{day_iso.replace('-','')}",
                     f"premium in/out {funds['prem_in']}/{funds['prem_out']}; short opt MV {funds['mv_short']}; "
                     f"B/F {funds['bf']} C/F {funds['cf']}; 平仓盈亏 field {funds['realized']} (exchange convention)"])
        n_fee = 0
        for t in trades:
            # correct fees on matching existing fills (same day, symbol, price, lots)
            r = con.execute("""UPDATE fills SET fee=?
                WHERE CAST(ts_utc AS DATE)=CAST(? AS DATE) AND lower(symbol)=lower(?) AND price=? AND qty=?""",
                [t["fee"], day_iso, t["instrument"], t["price"], int(t["lots"])])
            n_fee += 1
        # MO close economics (vs open price, x100 multiplier) — settlement books these via 权利金, not 平仓盈亏
        if day_iso == "2026-09-28":
            rows = con.execute("""SELECT fill_id, symbol, qty, price FROM fills
                WHERE CAST(ts_utc AS DATE)=CAST(? AS DATE) AND symbol LIKE 'MO2610%' AND action='BUY'
                ORDER BY symbol, fill_id""", [day_iso]).fetchall()
            p_econ, pi = [-11800.0, -11800.0, -11780.0], 0
            for fid, sym, qty, px in rows:
                if sym == "MO2610-C-7900":
                    rl = 5440.0 if int(qty) == 2 else 6000.0
                else:
                    rl = p_econ[pi]; pi += 1
                con.execute("UPDATE fills SET realized_pnl=? WHERE fill_id=?", [rl, fid])
                print(f"  MO close #{fid} {sym} qty {int(qty)} -> realized {rl:+.0f}")
        # per-lot margins for opens (持仓明细 groups) -> fills.margin_local
        for p in positions:
            con.execute("""UPDATE fills SET margin_local=?, margin_currency='CNY'
                WHERE CAST(ts_utc AS DATE)=CAST(? AS DATE) AND lower(symbol)=lower(?) AND qty=? AND margin_local IS NULL""",
                [p["margin"], day_iso, p["instrument"], int(p["lots"])])
        print(f"{day_iso}: equity {funds['equity']} margin {funds['margin']} avail {funds['avail']} "
              f"risk {funds['risk']}% fee {funds['fee']} | {len(trades)} trades, {len(positions)} position lots-groups")
        # MO opens seen in 持仓明细 but missing from fills -> insert (open time unknown, use date 15:00 CST)
        for p in positions:
            if "MO" not in p["instrument"]:
                continue
            exists = con.execute("""SELECT count(*) FROM fills WHERE symbol=? AND price=? AND qty=?""",
                                 [p["instrument"], p["open_px"], int(p["lots"])]).fetchone()[0]
            if exists:
                continue
            ts = dt.datetime.strptime(p["open_date"], "%Y%m%d").replace(hour=15) - dt.timedelta(hours=8)
            con.execute("""INSERT INTO fills (ts_utc, ts_orig, account, broker, underlying, symbol,
                security_type, expiry, strike, call_put, action, open_close, qty, price,
                exchange, currency, margin_local, margin_currency, source, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [ts, f"{p['open_date']} (time unknown)", ACCT, "浙商期货", "MO2610", p["instrument"],
                 "FUT_OPT", dt.date(2026, 10, 16), None, None, "SELL", "OPEN", int(p["lots"]), p["open_px"],
                 "中金所", "CNY", p["margin"], "CNY", "settlement_backfill",
                 f"from 结算单 持仓明细 ({p['product']}); open time not in statement; margin = lot-group"])
            print(f"  + MO open fill backfilled: {p['instrument']} {int(p['lots'])}@{p['open_px']} open {p['open_date']}")
    con.close()


if __name__ == "__main__":
    main()
