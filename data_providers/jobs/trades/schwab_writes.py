"""schwab_writes.py — the database half of a Schwab Account Statement.

Kept apart from the parsing so the parsers stay pure and testable without a
database, and so the `executemany` guards below live in the SAME function as the
calls they guard -- `check_regressions.py` searches for a guard only up to the
nearest enclosing FunctionDef, so a guard left at the caller reads as missing.

`executemany` raises on an empty list rather than doing nothing. That is not
hypothetical here: it is what destroyed MA's flow-days in build_option_flow, and a
quiet day with no orders is the normal case for this file.
"""
import datetime as dt

SOURCE = "schwab_account"

FILL_COLS = """ts_utc, ts_orig, account, broker, underlying, symbol, security_type,
    expiry, strike, call_put, action, open_close, qty, price, fee, realized_pnl,
    exchange, currency, source, notes"""
ORDER_COLS = """account, broker, underlying, symbol, action, open_close, order_type,
    validity, limit_price, qty, filled_qty, avg_fill_price, status, ts_submit_utc,
    currency, source, notes"""


def _dedupe_fills(con, fills):
    """Drop fills this source already holds, keyed on the notes string.

    Notes carries the exec time, the spread and the net price, so it identifies a leg
    within the file. Re-running over the whole directory is then a no-op, which is what
    makes "load everything in trading_logs" safe to run on every update.
    """
    have = {r[0] for r in con.execute(
        "SELECT notes FROM fills WHERE source=?", [SOURCE]).fetchall()}
    return [f for f in fills if (f["notes"] or "") not in have]


def _dedupe_orders(con, orders):
    have = {r[0] for r in con.execute(
        "SELECT notes FROM orders WHERE source=?", [SOURCE]).fetchall()}
    return [o for o in orders if (o["notes"] or "") not in have]


def write_account(con, fills, orders, pos, summary, account="40342049SCHW", day=None):
    """Write all four sections. Returns a per-table count for the caller to print."""
    day = day or dt.date.today()
    n_pos = n_fill = n_ord = 0

    if pos:
        con.executemany(
            "INSERT OR REPLACE INTO broker_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)", pos)
        n_pos = len(pos)

    new_fills = _dedupe_fills(con, fills) if fills else []
    if new_fills:
        # The row is built in FILL_COLS order and nothing else -- an earlier version
        # repeated account/broker/source before the list and again inside it, 27 values
        # for 20 columns, which DuckDB rejects at run time and no static check sees.
        con.executemany(
            f"INSERT INTO fills ({FILL_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [[f["ts_utc"], f["ts_orig"], account, "Schwab", f["underlying"], f["symbol"],
              "OPT", f["expiry"], f["strike"], f["call_put"], f["action"],
              f["open_close"], f["qty"], f["price"], None, None,
              None, "USD", SOURCE, f["notes"]] for f in new_fills])
        n_fill = len(new_fills)

    new_orders = _dedupe_orders(con, orders) if orders else []
    if new_orders:
        con.executemany(
            f"INSERT INTO orders ({ORDER_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [[account, "Schwab", o["underlying"], None, o["action"], o["open_close"],
              o["order_type"], "DAY", o["limit_price"], o["qty"], None, None,
              o["status"], o["ts_submit_utc"], "USD", SOURCE, o["notes"]]
             for o in new_orders])
        n_ord = len(new_orders)

    # One row per statement, not per section: `account_daily` is the equity series and
    # the statement's own Net Liquidating Value is the settlement truth for that day.
    if summary.get("Net Liquidating Value") is not None:
        con.execute(
            """INSERT OR REPLACE INTO account_daily
               (day, account, currency, net_liq, available, source, notes)
               VALUES (?,?,?,?,?,?,?)""",
            [day, account, "USD", summary.get("Net Liquidating Value"),
             summary.get("Option Buying Power"), SOURCE,
             f"Account Statement {day}"])
    return {"positions": n_pos, "fills": n_fill, "orders": n_ord}
