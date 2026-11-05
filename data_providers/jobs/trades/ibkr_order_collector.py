import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""ibkr_order_collector.py — capture the TWS completed-order lifecycle into `orders`.

WHY: reqCompletedOrders() only remembers orders completed during the CURRENT day on the
TWS machine's clock (CST) and resets at CST midnight. The US session (21:30–04:00 CST)
therefore straddles the reset, so the capture must run TWICE:

    ~23:50 CST  (before reset)  -> orders 21:30–24:00 CST   [else LOST]
    ~04:05 CST  (after close)   -> orders 00:00–04:00 CST (+ re-sees the rest)

Idempotent: each returned order replaces its own prior row (DELETE by order_id+source),
so re-running never duplicates and never wipes another window's rows.

Row mapping matches the Flex loader's layout (19 cols) so TWS and Flex rows are
comparable; `source='ibkr_tws_completed'` keeps them distinguishable. The completed
timestamp is TWS-local CST (raw value kept in notes) -> stored as UTC.

Usage:
  uv run python apps/trade_log/ibkr_order_collector.py \
      [--host host.docker.internal] [--port 4001] [--client-id 72] [--dry-run]
"""
import argparse
import datetime as dt
import pathlib
from zoneinfo import ZoneInfo

import duckdb
from data_providers.providers import tws
from ib_async import IB

ROOT = bootstrap.ROOT
DB = ROOT / "trades.duckdb"
SRC = "ibkr_tws_completed"
CST = ZoneInfo("Asia/Shanghai")
UNSET = 1.7976931348623157e+308   # IBKR's "no price" sentinel


def parse_completed_ts(raw):
    """TWS-local (CST) 'YYYYMMDD HH:MM:SS' -> naive UTC datetime, or None."""
    if not raw or len(str(raw)) < 17:
        return None
    s = str(raw)
    try:
        local = dt.datetime(int(s[:4]), int(s[4:6]), int(s[6:8]),
                            int(s[9:11]), int(s[12:14]), int(s[15:17]), tzinfo=CST)
    except ValueError:
        return None
    return local.astimezone(dt.timezone.utc).replace(tzinfo=None)


def row_for(co):
    c, o = co.contract, co.order
    # ib_async renamed the attribute; accept both so the collector survives either library.
    st = getattr(co, "orderStatus", None) or getattr(co, "orderState", None)
    lp = o.lmtPrice
    if lp is None or lp >= UNSET:
        lp = None
    status = (st.status or "")
    filled = o.totalQuantity if "Filled" in status else 0
    return [
        str(o.orderId), o.account, "IBKR",
        c.symbol, c.localSymbol or c.symbol,
        o.action, None, o.orderType, o.tif,
        lp, o.totalQuantity, filled, None,
        status, parse_completed_ts(getattr(st, "completedTime", None)),
        c.exchange, c.currency,
        f"permId={o.permId} completedStatus={getattr(st, 'completedStatus', None)} raw_completedTime={getattr(st, 'completedTime', None)}",
        SRC,
    ]


def main():
    ap = argparse.ArgumentParser()
    # Default None, not tws.PORT: an explicit --port still pins one, but with nothing
    # given we let tws.connect() try every TWS the user might be running. Defaulting to
    # a single port put the same wrong assumption in the CLI, where it is harder to see
    # -- the run failed on "connection refused" and looked like TWS was down.
    ap.add_argument("--host", default=tws.HOST)
    ap.add_argument("--port", type=int, default=None,
                    help="pin a port; omit to try Gateway (4001/4002) and desktop (7496/7497)")
    ap.add_argument("--client-id", type=int, default=72)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ib = IB()
    ib.RequestTimeout = 60
    if args.port:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=15, readonly=False)
    else:
        # readonly=False is deliberate here: this tool places nothing but does need the
        # order stream, which a read-only session does not expose.
        try:
            tws.connect(ib, client_id=args.client_id, timeout=15, readonly=False)
        except ConnectionError:
            # ...BUT a TWS configured with "Read-Only API" refuses every read-write
            # session at the socket (all ports "connection refused" while the desktop
            # is plainly up). Measured 2026-10-14: a read-only session answered
            # reqCompletedOrders fine (4 orders), so fall back rather than lose the day.
            tws.connect(ib, client_id=args.client_id, timeout=15, readonly=True)
    try:
        completed = ib.reqCompletedOrders(apiOnly=False)
    finally:
        ib.disconnect()

    rows = [row_for(co) for co in completed]
    print(f"completed orders returned: {len(rows)}")
    for r in rows:
        print(f"  #{r[0]:>8} {r[4] or r[3]:<22} {r[5]:<4} {r[7]:<8} "
              f"{'' if r[9] is None else r[9]:<8} x{r[10]} {r[13]:<10} {r[14]}")
    if args.dry_run:
        return
    con = duckdb.connect(str(DB))
    for r in rows:
        con.execute("DELETE FROM orders WHERE order_id=? AND source=?", [r[0], SRC])
        con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    con.close()
    print(f"stored {len(rows)} rows (source={SRC})")


if __name__ == "__main__":
    main()
