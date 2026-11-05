import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap above.

"""fetch_tws_ibkr.py — the IBKR account's positions and today's fills, from TWS.

WHY. Every IBKR figure in this repo came from a file the user exported by hand: the
journal, the Activity Statement CSV, the Flex XML, and PDFs of the Account window. The
log was therefore only as current as their last export -- on 2026-10-05 it was five days
stale while the account had been trading all week, and `positions` still held a CRWD
220P/230P structure that had since been rolled to Dec-18 225P. The API answers the same
question without anyone having to do anything.

WHAT IT WRITES.

  fills     one row per execution, source='ibkr_tws_exec'. execId is an execution's
            identity, so re-running replaces its own rows rather than duplicating.
  broker_positions   the broker's OWN position list, stamped with the read time. It is
            kept SEPARATE from `positions` on purpose: `positions` is our built book
            (greeks, marks, spots, delta_notional) and is the input to every risk number,
            while this is what the broker says we hold. Overwriting one with the other
            would destroy the ability to see them disagree -- which is the entire point
            of capturing it, since on 2026-10-05 they disagreed by a whole roll.

TWS KEEPS EXECUTIONS FOR THE CURRENT DAY ONLY, on the TWS machine's clock, so a US
session straddling its midnight loses its early part. Run this at least twice a day --
roughly 23:50 and 04:05 CST -- for complete fills. Positions have no such limit.

    uv run python apps/trade_log/fetch_tws_ibkr.py [--dry-run]
"""
import argparse
import datetime as dt
import pathlib
import sys

import duckdb

ROOT = bootstrap.ROOT
DB = ROOT / "trades.duckdb"
SOURCE = "ibkr_tws_exec"
ACCOUNT = "REPLACE_WITH_IBKR_ACCOUNT"


def fetch_account_data(ib):
    """Require the configured live account before reading its journal inputs."""
    from ib_async import ExecutionFilter

    if ACCOUNT not in ib.managedAccounts():
        raise RuntimeError("Configured live IBKR account is unavailable; refusing paper/other-account reconciliation")
    fills = ib.reqExecutions(ExecutionFilter(acctCode=ACCOUNT))
    positions = ib.reqPositions()
    return ([f for f in fills if f.execution.acctNumber == ACCOUNT],
            [p for p in positions if p.account == ACCOUNT])


def parse_expiry(c):
    s = (c.lastTradeDateOrContractMonth or "").strip()
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])) if len(s) >= 8 and s[:8].isdigit() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from ib_async import IB, ExecutionFilter
    from data_providers.providers import tws

    ib = tws.connect(IB(), client_id=222, timeout=25)
    try:
        raw_fills, raw_pos = fetch_account_data(ib)
    finally:
        ib.disconnect()

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if not args.dry_run:
        from data_providers.jobs.trades.broker_receipts import save_ibkr
        save_ibkr(raw_fills, raw_pos, now)
    fills = []
    for f in raw_fills:
        e, c = f.execution, f.contract
        if c.secType == "BAG":
            continue  # The receipt retains the summary; its option legs are counted below.
        comm = getattr(f.commissionReport, "commission", None) if f.commissionReport else None
        fills.append([
            e.time.replace(tzinfo=None) if e.time.tzinfo else e.time, str(e.time),
            e.acctNumber, "IBKR", c.symbol or c.localSymbol, c.localSymbol or c.symbol,
            c.secType or "OPT", parse_expiry(c),
            float(c.strike) if c.strike else None,
            (c.right or None) if c.secType in ("OPT", "FUT_OPT") else None,
            "BUY" if e.side.upper().startswith("B") else "SELL",
            # open_close is None on purpose, and the field is NOT missing by oversight.
            # Re-checked 2026-10-05 against a freshly restarted TWS: IBKR does not publish
            # it on ANY path we have.
            #   * Execution -- the object has no open/close attribute at all (19 fields:
            #     acctNumber avgPrice clientId cumQty evMultiplier evRule exchange execId
            #     lastLiquidity liquidation modelCode orderId orderRef pendingPriceRevision
            #     permId price shares side time).
            #   * Order.openClose -- the field EXISTS and TWS returns '' on every
            #     completed order; and every execution carries orderId=0, so there is no
            #     key to join executions to orders with even if it were populated.
            #   * Flex openCloseIndicator -- present as an attribute on all 328 <Trade>
            #     elements but empty or "-" on every one; the Flex `notes` codes are only
            #     P (partial) and L, never O/C.
            #   * The Trades CSV export has no such column.
            # Do NOT derive it from a running position over `fills`: that record has a
            # Sep-22..Oct-05 gap, and starting from zero mislabels the very first trade --
            # Schwab's own "SELL -6 SPXL TO CLOSE" comes out OPEN that way, which is how
            # you get 12 confidently wrong labels. Deriving it soundly needs a position
            # snapshot taken at the START of the session, which only forward-capture gives.
            None, abs(int(float(e.shares))), float(e.price),
            abs(float(comm)) if comm not in (None, "") else None,
            None, e.exchange, c.currency or "USD", SOURCE,
            f"execId {e.execId}; orderId {e.orderId}; permId {e.permId}; secType {c.secType}",
        ])

    # qty <> 0 only: TWS lists a contract that was opened and closed today as a residual
    # zero row, and storing those would make the broker list look larger than the book.
    pos = [[now, p.account, p.contract.symbol, p.contract.secType, p.contract.localSymbol,
            parse_expiry(p.contract),
            float(p.contract.strike) if p.contract.strike else None,
            p.contract.right or None, p.contract.currency or "USD", float(p.position)]
           for p in raw_pos if float(p.position) != 0]
    con = duckdb.connect(str(DB))
    con.execute("""CREATE TABLE IF NOT EXISTS broker_positions (
        as_of TIMESTAMP NOT NULL, account TEXT NOT NULL, broker TEXT NOT NULL,
        underlying TEXT, sec_type TEXT, symbol TEXT, expiry DATE, strike DOUBLE,
        call_put TEXT, currency TEXT, qty DOUBLE,
        PRIMARY KEY (as_of, account, symbol, expiry, strike, call_put))""")

    # The dedupe key is the execId inside `notes`. Comparing it against EVERY stored
    # note is O(n*m) and, worse, silently stopped working: three runs wrote 33 rows each
    # and the table reached 99. Building a set of the execIds once is both correct and
    # obviously so.
    have = {n.split(";")[0].strip() for n in
            (r[0] for r in con.execute("SELECT notes FROM fills WHERE source=?", [SOURCE]).fetchall())}
    # r[-1], not r[18]: the row carries 20 elements and notes sits LAST -- r[18] is
    # SOURCE, so the key was the constant "ibkr_tws_exec" and every re-run duplicated
    # all fills (found 2026-10-13, duplicated 3 rows).
    new = [r for r in fills if r[-1].split(";")[0].strip() not in have]
    if not new:
        print("  no new executions — all already recorded")

    print(f"  executions from TWS : {len(fills)}  ({len(new)} new)")
    print(f"  positions from TWS  : {len(pos)}")
    for r in pos:
        print(f"    {r[2]:6} {str(r[5]):10} {r[7] or '':2} {str(r[6] or ''):>8}  qty {r[9]:>6.0f}")

    # The comparison that makes this worth storing: broker truth vs our built book.
    ours = {(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT underlying, expiry, strike, call_put FROM positions "
        "WHERE broker='IBKR' AND qty<>0").fetchall()}
    theirs = {(r[2], r[5], r[6], r[7]) for r in pos}
    if ours != theirs:
        print("\n  !! THE BROKER AND OUR BOOK DISAGREE")
        for k in sorted(theirs - ours, key=str):
            print(f"    broker holds, we do not : {k}")
        for k in sorted(ours - theirs, key=str):
            print(f"    we hold, broker does not : {k}")
    else:
        print("\n  broker positions match our built book")

    if args.dry_run:
        print("\n  dry run — nothing written")
        con.close()
        return
    con.execute("BEGIN")
    if new:
        con.executemany("""INSERT INTO fills (ts_utc, ts_orig, account, broker, underlying,
            symbol, security_type, expiry, strike, call_put, action, open_close, qty, price,
            fee, realized_pnl, exchange, currency, source, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new)
    if pos:   # DuckDB raises on an empty list, not a no-op — a flat account is not an error
        con.executemany("""INSERT OR REPLACE INTO broker_positions
            (as_of, account, broker, underlying, sec_type, symbol, expiry, strike, call_put,
             currency, qty) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [[r[0], r[1], "IBKR", r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]] for r in pos])
    from data_providers.jobs.trades.position_costs import save_tws
    save_tws(con, raw_pos, now)
    con.execute("COMMIT")
    con.close()
    print(f"\n  wrote {len(new)} fills, {len(pos)} broker positions")


if __name__ == "__main__":
    main()
