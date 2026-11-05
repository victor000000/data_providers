import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap above.

"""fetch_tws_executions.py — TWS fills into `fills`, source='ibkr_tws_exec'.

WHY THIS EXISTS. The trade log's IBKR coverage came from two hand-carried routes: the
user's journal and the Activity Statement CSV. Both require the user to export and copy
a file, so the log is only as current as their last export -- it sat five days stale on
2026-10-05. reqExecutions() reaches the same account over the API and needs nobody to
do anything.

TWS KEEPS EXECUTIONS FOR THE CURRENT DAY ONLY, on the TWS machine's clock. Same
constraint as reqCompletedOrders, and the same consequence: a US session that straddles
the reset loses the early part, so this wants running more than once a day. It is
idempotent -- execId is the primary key of an execution, so a re-run replaces its own
rows and never duplicates.

    uv run python apps/trade_log/fetch_tws_executions.py [--dry-run]
"""
import argparse
import math
import datetime as dt
import pathlib
import sys

import duckdb

ROOT = bootstrap.ROOT
DB = ROOT / "trades.duckdb"
SOURCE = "ibkr_tws_exec"


def execution_fee(fill):
    """An unpopulated commission callback is unknown, not a zero fee."""
    report = fill.commissionReport
    if not report or report.execId != fill.execution.execId or not report.currency:
        return None
    if report.currency != fill.contract.currency:
        return None  # fills.fee has no independent currency field
    value = getattr(report, "commission", None)
    if value in (None, ""):
        return None
    value = float(value)
    return abs(value) if math.isfinite(value) and abs(value) < 1e100 else None


def parse_expiry(contract):
    """YYYYMMDD from the contract, or None -- stocks and combos have none."""
    s = (contract.lastTradeDateOrContractMonth or "").strip()
    if len(s) >= 8 and s[:8].isdigit():
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    from ib_async import IB, ExecutionFilter
    from data_providers.providers import tws

    ib = tws.connect(IB(), client_id=211, timeout=25)
    try:
        fills = ib.reqExecutions(ExecutionFilter())
    finally:
        ib.disconnect()
    if not fills:
        print("no executions returned — TWS keeps only the current day, and resets at "
              "its own midnight")
        return

    rows = []
    for f in fills:
        e, c = f.execution, f.contract
        # side is BOT/SLD from the account's view; qty is stored as a MAGNITUDE with
        # direction in `action`, which is the convention the rest of `fills` uses and
        # what selfcheck asserts ("qty is a magnitude, action carries direction").
        action = "BUY" if e.side.upper().startswith("B") else "SELL"
        rows.append([
            e.time.replace(tzinfo=None) if e.time.tzinfo else e.time,
            str(e.time),
            e.acctNumber,
            "IBKR",
            c.symbol or c.localSymbol,
            c.localSymbol or c.symbol,
            c.secType or "OPT",
            parse_expiry(c),
            float(c.strike) if c.strike else None,
            (c.right or None) if c.secType in ("OPT", "FUT_OPT") else None,
            action,
            None,                      # open_close: the execution does not say, and a
                                       # guessed OPEN/CLOSE would corrupt the FIFO pass
            abs(int(float(e.shares))),
            float(e.price),
            execution_fee(f),
            None,                      # realized_pnl is reconcile_fills' job, not ours
            e.exchange,
            c.currency or "USD",
            SOURCE,
            f"execId {e.execId}; orderId {e.orderId}; permId {e.permId}; "
            f"secType {c.secType}",
        ])

    con = duckdb.connect(str(DB))
    # execId is an execution's identity, so it is the dedupe key. Reading it back out of
    # notes is ugly, but `fills` has no exec_id column and adding one is a migration;
    # this keeps the loader correct now and the column can follow.
    have = {r[0] for r in con.execute(
        "SELECT notes FROM fills WHERE source=?", [SOURCE]).fetchall()}
    # r[-1], not r[18]: the row is 20 elements and notes sits LAST -- r[18] is SOURCE,
    # whose split(' ', 1)[1] is an IndexError (found 2026-10-13, first run with data).
    new = [r for r in rows if not any(f"execId {r[-1].split(';')[0].split(' ',1)[1]}" in (h or "")
                                      for h in have)]
    print(f"  {len(rows)} executions from TWS, {len(new)} not already in fills")
    for r in new[:8]:
        print(f"    {str(r[0])[:16]}  {r[5][:22]:22} {r[10]:4} {r[12]:>4} @ {r[13]:>8}  {r[2]}")
    if len(new) > 8:
        print(f"    ... {len(new)-8} more")
    if args.dry_run:
        print("  dry run — nothing written")
        con.close()
        return
    if new:
        con.executemany("""INSERT INTO fills (ts_utc, ts_orig, account, broker, underlying,
            symbol, security_type, expiry, strike, call_put, action, open_close, qty, price,
            fee, realized_pnl, exchange, currency, source, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new)
        print(f"  wrote {len(new)} rows to fills (source='{SOURCE}')")
    con.close()


if __name__ == "__main__":
    main()
