"""bars.py — write Sina option quotes into cn.option_bars, for the venues that need them.

DCE and CFFEX MO had no option bars at all: DCE's own endpoint answers HTTP 412 behind a bot
wall and akshare reports it as JSONDecodeError, and MO's Sina series only ever reached the
solver. Since the solver already downloads those exact contracts, the bars ride along with the
IV fetch instead of costing a second pass over the same rate-limited source.

What is NOT filled, deliberately: settle, oi, iv and delta. Sina's per-contract history is
date/open/high/low/close/volume and nothing else, so those columns stay NULL rather than
being back-filled from our own solver -- a stored IV that is really the exchange's number in
one row and ours in the next cannot be told apart later, which is the mixing this whole
package refuses to do.
"""
import duckdb

from data_providers.jobs.data_ops.fetch_cn_products import parse_code

COLS = ("source, contract, underlying, expiry, strike, call_put, day, open, high, low, close, "
        "volume, vwap, venue, product, oi, settle, iv, delta")


def rows_for(venue, prod, fmt, bars, cal=None):
    """[(19-tuple)] for one product's collected {contract, day: (o,h,l,c,v)}."""
    src = f"sina_{prod.lower()}_bars"
    out, dropped = [], 0
    for (code, day), (o, h, low, c, v) in bars.items():
        p = parse_code(code, fmt, venue, cal)
        if not p:
            dropped += 1
            continue
        root, exp, cp, k = p
        out.append((src, code, root, exp, k, cp, day, o, h, low, c, v, None,
                    venue, prod, None, None, None, None))
    if dropped:
        print(f"      {venue}:{prod}: {dropped} bar row(s) had an unparseable contract code",
              flush=True)
    return out


def write_bars(con, rows):
    """DELETE-then-INSERT per (venue, product, contract, day), so a re-run replaces."""
    if not rows:
        return 0
    con.execute("BEGIN")
    con.executemany(f"""DELETE FROM option_bars WHERE venue=? AND product=? AND contract=?
                        AND day=?""",
                    [(r[13], r[14], r[1], r[6]) for r in rows])
    con.executemany(f"INSERT INTO option_bars ({COLS}) VALUES ({','.join(['?'] * 19)})", rows)
    con.execute("COMMIT")
    return len(rows)


def covered_days(con, venue, prod):
    """{day} already stored for this venue+product -- the resume set for a re-run."""
    return {r[0] for r in con.execute(
        "SELECT DISTINCT day FROM option_bars WHERE venue=? AND product=?",
        [venue, prod]).fetchall()}


def covered_contracts(con, venue, prod):
    """{contract} whose bars are CURRENT for this venue+product -- the skip set for --bars.

    "HAS ANY ROW" IS A ONE-SHOT LOCK, and with --bars the solver narrows every month to the
    codes NOT in this set — so once a contract had a single row, no month containing it was
    ever fetched again. Measured 2026-10-11: DCE M/Y/C/A/PG/... and GFEX LC/SI each reported
    "nothing new" while their IV sat six sessions stale (bars frozen at 2026-09-30, the newest
    session being 10-09). An EXPIRED contract stops early by nature and stays skipped, so this
    does not re-buy finished ladders.

    THE ANCHOR IS THE WAREHOUSE'S NEWEST DAY, NOT THE PRODUCT'S OWN. Anchoring per product is
    self-referential and blind in the one case that matters: with LC/SI frozen at 09-30 their
    own newest day WAS 09-30, every contract looked current, and the re-run produced 0 points
    for both. Against the global anchor (2026-10-09, from products that are current) the same
    two yield 176 and 110 contracts to fetch — and products that are already current are
    unchanged (SHFE:AG 0, CZCE:MA 28).
    """
    return {r[0] for r in con.execute(
        """SELECT contract FROM option_bars WHERE venue=? AND product=?
           GROUP BY contract
          HAVING max(expiry) < (SELECT max(day) FROM option_bars)
              OR max(day) >= (SELECT max(day) FROM option_bars) - INTERVAL 5 DAY""",
        [venue, prod]).fetchall()}


def save_product(con, venue, prod, fmt, src, points, bars=None, cal=None):
    """One product's IV points, plus its option bars when the run collected them."""
    from data_providers.jobs.history.cn_iv.dump import write_points
    write_points(con, prod, src, points)
    if not bars:
        return len(points), 0
    # NOT indented under the guard above: that made this return unreachable dead code AND
    # made the function fall through to None whenever bars WAS provided -- so a bars run
    # collected thousands of rows, printed their count, and wrote none of them. Found
    # 2026-10-07 by checking the database instead of the log line that reports what was
    # COLLECTED. The print was reading len(bars) either way.
    return len(points), write_bars(con, rows_for(venue, prod, fmt, bars, cal))
