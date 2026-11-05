"""schwab_account_parse.py — the execution and order sections of a statement.

Pure text -> dicts, no database: `schwab_writes` owns the writing. Both parsers take
the export clock's offset, recovered from the file rather than assumed -- the export
is stamped on the SESSION's clock (CST here, UTC+8), and reading it as ET puts every
fill four hours in the future, which is a real bug this loader's sibling already had.
"""
from data_providers.jobs.trades import load_schwab_csv as sw
from data_providers.jobs.trades.schwab_statement import occ, _when

from data_providers.jobs.trades import load_schwab_csv as sw

ACCOUNT = sw.ACCOUNT



def parse_fills(rows, offset):
    """One row per LEG. A spread is two rows sharing an exec time, as Schwab prints it.

    The Net Price column belongs to the spread, not the leg, so it goes into `notes`
    rather than being written as a leg price.
    """
    out, last = [], None
    for r in rows:
        r = list(r) + [""] * (12 - len(r))
        # A SPREAD IS TWO ROWS, and only the first carries the exec time: Schwab writes
        # the second leg with blank Time/Spread/Net cells under the first. Skipping rows
        # without a time therefore recorded ONE leg of every spread -- half of this
        # session, and the missing half is the one that makes the position whole. Carry
        # the time (and the spread and net price) down instead. The `notes` key would
        # also have collided across every continuation row, since all three cells are
        # blank, so deduplication on re-run needs this too.
        if _when(r[1]):
            last = (r[1].strip(), r[2].strip(), r[11].strip())
        if not r[6].strip() or last is None:
            continue
        exec_time, spread, net = (r[1].strip(), r[2].strip(), r[11].strip())
        if not exec_time:
            exec_time, spread, net = last
        ts = _when(exec_time)
        if not ts:
            continue
        sym, exps, K, typ = r[6].strip(), r[7].strip(), r[8].strip(), r[9].strip().upper()
        if typ not in ("PUT", "CALL"):
            continue
        cp = typ[0]
        qty = int(sw.money(r[4]) or 0)
        out.append({
            "ts_utc": ts - offset, "ts_orig": exec_time, "underlying": sym,
            "expiry": sw.parse_expiry(exps), "strike": sw.money(K), "call_put": cp,
            "symbol": occ(sym, sw.parse_expiry(exps), K, cp),
            "action": r[3].strip().upper(), "qty": abs(qty),
            "open_close": "CLOSE" if "CLOSE" in r[5].upper() else "OPEN",
            "price": sw.money(r[10]),
            "notes": f"{spread} {exec_time}; net {net}; {r[3].strip()} {K}{cp}",
        })
    return out


def parse_orders(rows, offset):
    """The order lifecycle, including the rows that never filled.

    CANCELED and REJECTED are kept: an order that was rejected for buying power is a
    fact about the account, and eleven of them in one session is the interesting part
    of this file. `orders.status` carries it.
    """
    out = []
    for r in rows:
        # ONE LEADING BLANK COLUMN. The header is
        # `Notes,,Time Placed,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,PRICE,,TIF,Status`
        # -- there is an empty second field and a second empty one before TIF -- so reading
        # time at index 1 (as the Trade History section needs) matched nothing and this
        # returned zero orders for a day that had 30 of them. Indices here are the header's,
        # offset by that blank.
        r = list(r) + [""] * (15 - len(r))
        ts = _when(r[2])
        if not ts:
            continue
        out.append({
            "ts_submit_utc": ts - offset, "underlying": r[7].strip(),
            "action": r[4].strip().upper(), "qty": sw.money(r[5]),
            "open_close": "CLOSE" if "CLOSE" in r[6].upper() else "OPEN",
            "order_type": r[3].strip(), "limit_price": sw.money(r[11]),
            "status": r[14].strip().split(":")[0].strip().upper(),
            "notes": f"{r[3].strip()} {r[2].strip()}; {r[14].strip()}",
        })
    return out
