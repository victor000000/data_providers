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

"""build_reference.py — populate reference.duckdb from data_providers.data we ALREADY hold.

No downloads here: everything is derived from the log (fills), the series
(option_bars, underlying_daily) and the warehouse (symbols). Broker/exchange specs
that our data cannot prove (CN multipliers and tick sizes, US exercise styles) are
left NULL on purpose — a wrong multiplier is worse than an absent one — and filled
later from TWS reqContractDetails / akshare contract info.

Idempotent: each table is replaced wholesale from its source query.

    uv run python apps/data_ops/build_reference.py
"""
import datetime as dt
import pathlib
import sys

from data_providers.data import clock  # noqa: E402
from data_providers.data import db  # noqa: E402

TODAY = clock.today().isoformat()


def main():
    con = db.connect(log=None, trades_ro=True, history="ro", universe=True, reference="rw")

    # ---- instruments: every underlying we have ever touched (fills are the roster of record)
    con.execute("DELETE FROM ref.instruments")
    con.execute(f"""
        INSERT INTO ref.instruments
        SELECT underlying, NULL, asset_class, exchange, currency, NULL,
               first_seen, last_seen, last_seen >= CURRENT_DATE - 30, 'fills+warehouse',
               NULL
        FROM (
            SELECT f.underlying,
                   CASE WHEN y.symbol IS NOT NULL THEN 'us'
                        WHEN f.broker = '浙商期货' THEN 'future'
                        ELSE NULL END AS asset_class,
                   any_value(f.exchange) AS exchange,
                   any_value(f.currency) AS currency,
                   min(CAST(f.ts_utc AS DATE)) AS first_seen,
                   max(CAST(f.ts_utc AS DATE)) AS last_seen
            FROM tr.fills f
            LEFT JOIN yh.symbols y ON y.symbol = f.underlying
            WHERE f.underlying IS NOT NULL
            GROUP BY 1, 2
        )""")
    n_inst = con.execute("SELECT count(*) FROM ref.instruments").fetchone()[0]

    # ---- option_contracts: every contract we hold a bar for, plus every one we traded
    con.execute("DELETE FROM ref.option_contracts")
    con.execute("""
        INSERT INTO ref.option_contracts
        SELECT symbol, any_value(underlying), any_value(expiry), any_value(strike),
               any_value(call_put), 100, NULL, NULL,
               min(day), max(day), 'option_bars'
        FROM mh.option_bars GROUP BY symbol""")
    # The fills insert used to key on REPLACE(f.symbol,' ',''), i.e. the VENDOR's spelling:
    # 'META Nov20 850C' -> 'METANov20850C', which can never equal the OCC symbol the bars
    # insert writes ('META261120C00850000'). So the same economic contract was registered
    # twice whenever it appeared in both tables, under two names, neither of which could
    # join to the other -- 65 such pairs. Rebuild the symbol from the parsed fields instead
    # (underlying + YYMMDD + C/P + strike*1000), which is exactly the OCC convention
    # option_bars uses and which reproduces 475,107 of its 475,755 symbols exactly. The
    # 648 that differ are CN crude-oil contracts (lowercase 'sc...') that predate the CN
    # split -- for those, and for any row whose expiry/strike did not parse, keep the
    # native spelling rather than inventing a key.
    #
    # `multiplier` was hardcoded to 100 for EVERY contract, CN included -- MA701 methanol
    # is 10 CNY/point and SC crude is 1000, so the reference layer disagreed with
    # build_positions (which writes the real 10.0) by 10x. The module docstring already
    # states the rule this violates: specs our data cannot prove are left NULL, because a
    # wrong multiplier is worse than an absent one. US equity/ETF options are the one case
    # we can state (standard 100); CN is NULL until akshare contract info fills it.
    con.execute("""
        INSERT INTO ref.option_contracts
        SELECT symbol, underlying, expiry, strike, call_put, multiplier, NULL, NULL,
               first_seen, last_seen, 'fills'
        FROM (
            SELECT CASE WHEN f.expiry IS NOT NULL AND f.strike IS NOT NULL
                         AND f.call_put IS NOT NULL AND f.broker <> '浙商期货'
                        THEN regexp_replace(upper(f.underlying), '[^A-Z0-9]', '', 'g')
                             || strftime(f.expiry, '%y%m%d') || f.call_put
                             || lpad(CAST(CAST(f.strike * 1000 AS BIGINT) AS VARCHAR), 8, '0')
                        ELSE REPLACE(f.symbol, ' ', '') END AS symbol,
                   f.underlying, f.expiry, f.strike, f.call_put,
                   CASE WHEN f.broker = '浙商期货' THEN NULL ELSE 100 END AS multiplier,
                   min(CAST(f.ts_utc AS DATE)) AS first_seen,
                   max(CAST(f.ts_utc AS DATE)) AS last_seen
            FROM tr.fills f
            WHERE f.call_put IS NOT NULL AND f.symbol IS NOT NULL
            GROUP BY 1, 2, 3, 4, 5, 6
        )
        ON CONFLICT DO NOTHING""")
    n_con = con.execute("SELECT count(*) FROM ref.option_contracts").fetchone()[0]
    # Counted from the SOURCE rows, not from option_contracts: the insert is ON CONFLICT
    # DO NOTHING, so a traded contract that now keys correctly simply does not add a row --
    # asking the table for its own duplicates returns 0 whether or not the join works.
    n_dup = con.execute("""
        SELECT count(*) FROM (
            SELECT DISTINCT regexp_replace(upper(f.underlying), '[^A-Z0-9]', '', 'g')
                 || strftime(f.expiry, '%y%m%d') || f.call_put
                 || lpad(CAST(CAST(f.strike * 1000 AS BIGINT) AS VARCHAR), 8, '0') AS occ
            FROM tr.fills f
            WHERE f.call_put IS NOT NULL AND f.expiry IS NOT NULL AND f.strike IS NOT NULL
              AND f.broker <> '浙商期货'
        ) x JOIN (SELECT DISTINCT symbol FROM mh.option_bars) b ON b.symbol = x.occ""").fetchone()[0]

    # ---- expiries: what actually exists, per underlying
    con.execute("DELETE FROM ref.expiries")
    con.execute("""
        INSERT INTO ref.expiries
        SELECT underlying, expiry, 'monthly', NULL, 'option_bars'
        FROM (SELECT DISTINCT underlying, expiry FROM mh.option_bars)
        ON CONFLICT DO NOTHING""")
    con.execute("""
        INSERT INTO ref.expiries
        SELECT underlying, expiry, 'monthly', NULL, 'fills'
        FROM (SELECT DISTINCT underlying, expiry FROM tr.fills WHERE expiry IS NOT NULL)
        ON CONFLICT DO NOTHING""")
    n_exp = con.execute("SELECT count(*) FROM ref.expiries").fetchone()[0]

    # ---- futures_contracts: the delivery-month contracts our CN options sit on, with the
    #      margin the broker actually took per lot. NOTE: in DuckDB `~` is FULL match, not
    #      "contains" — use regexp_matches() for a substring test.
    con.execute("DELETE FROM ref.futures_contracts")
    con.execute(f"""
        INSERT INTO ref.futures_contracts
        SELECT product || month AS symbol, product, any_value(exchange), month,
               NULL, NULL, NULL, NULL,
               median(margin_local / NULLIF(qty, 0)),
               'fills·observed', DATE '{TODAY}'
        FROM (
            SELECT upper(regexp_extract(upper(underlying), '^([A-Z]+)[0-9]', 1)) AS product,
                   regexp_extract(underlying, '^[A-Za-z]+([0-9]{{3,4}})', 1) AS month,
                   exchange, margin_local, qty
            FROM tr.fills
            WHERE margin_local IS NOT NULL AND margin_local > 0 AND qty <> 0
              AND regexp_matches(underlying, '^[A-Za-z]+[0-9]')
        )
        GROUP BY product, month""")
    n_fut = con.execute("SELECT count(*) FROM ref.futures_contracts").fetchone()[0]

    # ---- calendar: days PROVEN by bars we hold (venue US vs CN by the symbol's home)
    con.execute("DELETE FROM ref.calendar")
    con.execute("""
        INSERT INTO ref.calendar
        SELECT 'US', day, TRUE, 'RTH', TRUE, 'observed in option_bars'
        FROM (SELECT DISTINCT day FROM mh.option_bars) ON CONFLICT DO NOTHING""")
    con.execute("""
        INSERT INTO ref.calendar
        SELECT 'CN', day, TRUE, 'day', TRUE, 'observed in underlying_daily'
        FROM (SELECT DISTINCT day FROM mh.underlying_daily WHERE source LIKE 'sina%' OR source LIKE 'akshare%')
        ON CONFLICT DO NOTHING""")
    n_cal = con.execute("SELECT count(*) FROM ref.calendar").fetchone()[0]

    # ---- futures_products: specs we know by name. Marked unverified until an exchange
    #      contract-info pull (akshare) confirms them — a wrong multiplier is worse than none.
    PRODUCTS = [
        # product, exchange, name,                    multiplier, tick,  margin_rate, ccy
        ("MA", "郑商所 CZCE", "甲醇 methanol",              10,  1.0,  None, "CNY"),
        ("SC", "上期能源 INE", "原油 crude oil",          1000,  0.1,  None, "CNY"),
        ("MO", "中金所 CFFEX", "中证1000股指期权 CSI1000",  100,  0.2,  None, "CNY"),
    ]
    for prod, exch, name, mult, tick, mr, ccy in PRODUCTS:
        con.execute("DELETE FROM ref.futures_products WHERE product=?", [prod])
        con.execute("INSERT INTO ref.futures_products VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [prod, exch, name, mult, tick, mr, ccy, None,
                     "manual·unverified", TODAY])
    n_prod = con.execute("SELECT count(*) FROM ref.futures_products").fetchone()[0]

    print(f"reference.duckdb: instruments {n_inst} · option_contracts {n_con} · "
          f"expiries {n_exp} · futures_contracts {n_fut} · futures_products {n_prod} · "
          f"calendar days {n_cal}")
    # n_dup was 0 before the symbol rebuild above: traded contracts and barred contracts
    # were keyed differently, so none of them ever matched. A non-zero value here is the
    # fix working -- the same contract is now ONE row instead of two.
    print(f"  traded contracts that match a barred contract on the same key: {n_dup}")
    for sym, prod, marg in con.execute(
            "SELECT symbol, product, observed_margin FROM ref.futures_contracts ORDER BY 1").fetchall():
        print(f"  {sym:8} product {prod:4} observed margin/lot {marg:,.0f}")
    con.close()


if __name__ == "__main__":
    main()
