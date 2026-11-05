import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

# ── --help guard ─────────────────────────────────────────────────────────────
# No argparse here, so `script.py --help` used to execute this script in full.
# That is not hypothetical: a 2026-10-05 sweep ran every app with --help and
# rewrote `fills`, `risk_profile` and `positions`. The write does not have to be
# in THIS file either -- live_risk_profile.py orchestrates build_positions and
# risk_profile through subprocess, so "does it INSERT?" is the wrong test. The
# rule is simply that a script with no argument parser must not run on --help.
from data_providers.shared import helpguard
helpguard.handle_help(__file__)   # reads the module summary from the SOURCE:
# __doc__ is None here -- the description sits after the bootstrap below, and a
# docstring must be the first statement, so all 33 copies printed "(no docstring)".

"""reconcile_fills.py — FIFO realized-P&L reconciliation for close fills missing it.

Walks each (account, contract) in time order; matches closes against open lots FIFO and
computes realized = (open_px - close_px) * matched_qty * multiplier (short side sign) or
(close_px - open_px) * ... for longs. GROSS of fees (IBKR fifoPnlRealized convention).
Only fills with realized_pnl IS NULL are written; everything else is left untouched.

Multipliers: US options 100, US stocks 1; CN: SC 1000, MA/MO 10/100 (from settlement data context).
Usage: uv run python reconcile_fills.py [--dry-run]
"""
import re
import sys

import duckdb

DB = "trades.duckdb"
MULT = {"20101589": {"sc": 1000.0, "ma": 10.0, "mo": 100.0}, "REPLACE_WITH_IBKR_ACCOUNT": {"*": 100.0}, "40342049SCHW": {"*": 100.0}}
OPT_SYM = re.compile(r"^([A-Z.]+)\s+(\d{1,2}[A-Z]{3}\d{2}|\d{2}/\d{2}/\d{4})\s+([\d.]+)\s*([CP])$")


def key_of(account, symbol, sec_type):
    """contract key: (underlying, expiry-ish, strike, cp) normalized across symbol styles; stocks -> (sym,)."""
    if sec_type == "STOCK" or (sec_type or "").startswith("STOCK"):
        return (symbol.upper(),)
    m = re.match(r"^([A-Z.]+)\s+(\d{1,2})([A-Z]{3})(\d{2})\s+([\d.]+)\s+([PC])$", symbol or "")
    if m:
        und, d, mon, yr, K, cp = m.groups()
        return (und, f"{yr}{mon}{int(d):02d}", float(K), cp)
    m = re.match(r"^([A-Z.]+)\s+(\d{2})/(\d{2})/(\d{4})\s+([\d.]+)\s+([CP])$", symbol or "")
    if m:
        und, mm, dd, yyyy, K, cp = m.groups()
        return (und, f"{yyyy[2:]}{mm}{dd}", float(K), cp)
    m = re.match(r"^([A-Za-z.]+)\s+([A-Za-z]{3})(\d{1,2})\s+([\d.]+)\s*([PC])$", symbol or "")
    if m:
        und, mon, d, K, cp = m.groups()
        return (und.upper(), f"26{mon.upper()}{int(d):02d}", float(K), cp)
    return (symbol or "?",)


def main():
    dry = "--dry-run" in sys.argv
    con = duckdb.connect(DB)
    rows = con.execute("""SELECT fill_id, account, symbol, security_type, action, open_close, qty,
            price, realized_pnl, CAST(ts_utc AS DATE) d FROM fills
        WHERE ts_utc IS NOT NULL ORDER BY account, d, fill_id""").fetchall()
    lots = {}          # (account, key) -> list of [remaining_qty, price, side]
    updates = []
    for fid, acct, sym, sec, action, oc, qty, px, rl, d in rows:
        if px is None or qty is None:
            continue
        key = (acct, key_of(acct, sym, sec))
        book = lots.setdefault(key, [])
        side = "SHORT" if action == "SELL" else "LONG"
        if oc == "OPEN":
            book.append([abs(qty), px, side])
        elif oc == "CLOSE":
            # Consume lots for EVERY close, including ones that already carry a
            # realized_pnl. The old condition (`and rl is None`) skipped them
            # entirely, so their lots stayed on the book and a LATER unreconciled
            # close matched against positions that had already been sold:
            #   OPEN 10@5, OPEN 10@2, CLOSE 10@4 (already booked), CLOSE 10@3
            # booked the second close against the 5.00 lot (+$40/contract) instead
            # of the 2.00 lot (-$10), and wrote that wrong number to
            # fills.realized_pnl. The `elif oc in ("OPEN","CLOSE"): pass` below even
            # carried the comment "track closes that already had pnl too" -- the
            # intent was recorded, the bookkeeping was not.
            need = abs(qty)
            realized = 0.0
            for lot in book:
                if need <= 0:
                    break
                if lot[0] <= 0 or lot[2] == side:
                    continue
                take = min(lot[0], need)
                mult = 100.0
                if acct == "20101589":
                    base = sym.lower()[:2]
                    mult = MULT["20101589"].get(base, 100.0)
                elif sec and "STOCK" in sec.upper():
                    mult = 1.0
                if lot[2] == "SHORT":
                    realized += (lot[1] - px) * take * mult
                else:
                    realized += (px - lot[1]) * take * mult
                lot[0] -= take
                need -= take
            if need == 0:
                # Only WRITE when the row has no value yet; a close that already
                # carries one keeps it. The lot bookkeeping above happens either way.
                if rl is None:
                    updates.append((round(realized, 2), fid))
                    if not dry:
                        con.execute("UPDATE fills SET realized_pnl=? WHERE fill_id=?",
                                    [round(realized, 2), fid])
            else:
                print(f"  ! incomplete lots for #{fid} {sym} ({need} unmatched)")
    print(f"realized_pnl written for {len(updates)} close fills (dry={dry})")
    if updates:
        tot = sum(u[0] for u in updates)
        print(f"  total realized now booked: {tot:+,.2f}")
    con.close()


if __name__ == "__main__":
    main()
