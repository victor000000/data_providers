import pathlib
import sys

from data_providers import runtime as bootstrap  # noqa: F401

"""build_equity_events.py — one event history per name: the thing event studies need.

Unifies, for every underlying we touch, what happened TO it and when:

  earnings        ref.earnings (Futu + yfinance)        date, BMO/AMC, EPS estimate/actual
  dividend        yh.dividends (the warehouse owns it) ex-date + amount
  split           yh.splits                             date + ratio
  index_add/drop  yh.index_membership                    which index, which side

All sources are independent of the price bars, so a "what did it do around its last
eight earnings" query is an event study over stored facts, not a re-derivation.
Idempotent: DELETE per underlying then insert.

    uv run python apps/data_ops/build_equity_events.py [--symbols ...]
"""
import argparse
import pathlib
import sys

from data_providers.data import db  # noqa: E402

ROOT = bootstrap.ROOT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=None)
    args = ap.parse_args()

    con = db.connect(log=None, reference="rw", universe=True)
    syms = args.symbols or [r[0] for r in con.execute(
        "SELECT underlying FROM ref.instruments ORDER BY 1").fetchall()]

    total = 0
    for sym in syms:
        rows = []
        for day, timing, est, act in con.execute(
                "SELECT day, timing, eps_estimate, eps_actual FROM ref.earnings WHERE underlying=?",
                [sym]).fetchall():
            detail = f"{timing or 'n/a'} fiscal report" + (f" · EPS est {est}" if est else "") + \
                     (f" · actual {act}" if act else "")
            rows.append((sym, day, "earnings", detail, "futu/yfinance"))
        for day, amt in con.execute("SELECT date, amount FROM yh.dividends WHERE symbol=?", [sym]).fetchall():
            rows.append((sym, day, "dividend", f"{amt}", "yahoo warehouse (ex-date)"))
        for day, ratio in con.execute("SELECT date, ratio FROM yh.splits WHERE symbol=?", [sym]).fetchall():
            rows.append((sym, day, "split", f"ratio {ratio}", "yahoo warehouse"))
        for idx, start, end in con.execute(
                """SELECT index_slug, start_date, end_date FROM yh.index_membership
                   WHERE symbol=?""", [sym]).fetchall():
            if start:
                rows.append((sym, start, "index_add", idx, "yahoo warehouse"))
            if end:
                rows.append((sym, end, "index_drop", idx, "yahoo warehouse"))
        con.execute("DELETE FROM ref.equity_events WHERE underlying=?", [sym])
        if rows:
            con.executemany("INSERT INTO ref.equity_events VALUES (?,?,?,?,?) "
                            "ON CONFLICT DO NOTHING", rows)
        total += len(rows)
        print(f"  {sym:6} {len(rows):>6} events")

    n = con.execute("SELECT count(*) FROM ref.equity_events").fetchone()[0]
    print(f"equity_events: {total} written, {n} rows stored")
    print("\nmost recent events for held names:")
    for r in con.execute("""
            SELECT underlying, day, kind, detail FROM ref.equity_events
            WHERE underlying IN ('META','CRWD','SWKS','TLT','EWZ','ILMN','NBIS','SNDK','SPY','QQQ')
              AND day >= CURRENT_DATE - 120 ORDER BY day DESC LIMIT 12""").fetchall():
        print("  ", r)
    con.close()


if __name__ == "__main__":
    main()
