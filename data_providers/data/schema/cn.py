
from data_providers import runtime as bootstrap

"""cn_schema.py — the CN database schema, with venue and product as first-class columns.

Implements plans/CN_SCHEMA_DESIGN.md. The point of the schema is that the thing which caused
the 20-year CZCE bug cannot be expressed in it:

    contract codes differ per venue (CZCE 'MA611' is YMM, SHFE 'sc2612' is YYMM), and the old
    table kept only the concatenated symbol — so the venue was recoverable only by string
    inspection, and a parser that guessed wrong corrupted 152,304 rows silently.

Here `venue` and `product` are columns, and the key is (venue, product, contract, day). A CZCE
MA row and a hypothetical US MA row are structurally incapable of colliding.

`cn_product_map.code_format` holds the convention as DATA, so adding a venue is a row rather
than an edit to parsing logic.

    uv run --no-project --with duckdb python scripts/cn_schema.py --init      # create
    uv run --no-project --with duckdb python scripts/cn_schema.py --migrate   # backfill
"""
import argparse

import duckdb

ROOT = bootstrap.ROOT
CN = ROOT / "cn_market.duckdb"

from data_providers.data.schema.cn_products import PRODUCTS
from data_providers.data.schema.futures import CN_OPTION_CHECK_DDL, CN_QUOTE_DDL
from data_providers.data.schema.history import DDL as HISTORY_DDL

DDL = [
    # option bars, one row per contract per day
    """CREATE TABLE IF NOT EXISTS option_bars (
        source TEXT, contract TEXT, underlying TEXT, expiry DATE, strike DOUBLE, call_put TEXT,
        day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, vwap DOUBLE,
        venue TEXT, product TEXT, oi DOUBLE, settle DOUBLE, iv DOUBLE, delta DOUBLE,
        PRIMARY KEY (source, contract, day))""",
    """CREATE TABLE IF NOT EXISTS iv_history (
        day DATE, underlying TEXT, expiry DATE, atm_iv DOUBLE, spot DOUBLE, source TEXT,
        PRIMARY KEY (day, underlying, source))""",
    # the underlyings: CN futures themselves
    """CREATE TABLE IF NOT EXISTS futures_daily (
        venue TEXT, product TEXT, contract TEXT, day DATE,
        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, settle DOUBLE,
        volume DOUBLE, oi DOUBLE, source TEXT,
        PRIMARY KEY (venue, product, contract, day))""",
    # how to FETCH each product — the convention as data, not as parser branches
    """CREATE TABLE IF NOT EXISTS cn_product_map (
        canonical TEXT, venue TEXT, product TEXT, ak_symbol TEXT, endpoint TEXT,
        code_format TEXT, price_cols TEXT, note TEXT,
        PRIMARY KEY (canonical))""",
]
DDL.append(CN_QUOTE_DDL)
DDL.append(CN_OPTION_CHECK_DDL)
DDL.append(next(sql.format(p='') for sql in HISTORY_DDL
                if 'CREATE TABLE IF NOT EXISTS {p}option_flow_daily' in sql))



def init(con):
    for d in DDL:
        con.execute(d)
    for canon, venue, prod, ak, ep, fmt, cols in PRODUCTS:
        con.execute("INSERT OR REPLACE INTO cn_product_map VALUES (?,?,?,?,?,?,?,?)",
                    [canon, venue, prod, ak, ep, fmt, cols,
                     f"{venue} {prod}: code format {fmt}"])
    print(f"  schema created; product_map seeded with {len(PRODUCTS)} products"
          f" across {len({p[1] for p in PRODUCTS})} venues")


def migrate(con):
    """Backfill venue/product into the existing rows, from `source`.

    `source` already encodes both (`akshare_czce_ma` -> CZCE + MA), so the information was
    never lost — it was just not a column. Recovering it is exact, not a guess.
    """
    exists = {r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables()").fetchall()}
    if "option_bars" not in exists:
        print("  nothing to migrate"); return
    cols = {r[0].lower() for r in con.execute("DESCRIBE option_bars").fetchall()}
    if "venue" in cols:
        print("  already has venue/product — nothing to backfill"); return

    con.execute("ALTER TABLE option_bars ADD COLUMN venue TEXT")
    con.execute("ALTER TABLE option_bars ADD COLUMN product TEXT")
    con.execute("ALTER TABLE option_bars ADD COLUMN oi DOUBLE")
    con.execute("ALTER TABLE option_bars ADD COLUMN settle DOUBLE")
    con.execute("ALTER TABLE option_bars ADD COLUMN iv DOUBLE")
    con.execute("ALTER TABLE option_bars ADD COLUMN delta DOUBLE")
    # source looks like akshare_<venue>_<product>
    con.execute("""
        UPDATE option_bars SET
            venue   = upper(split_part(source, '_', 2)),
            product = upper(split_part(source, '_', 3))
        WHERE venue IS NULL""")
    n = con.execute("SELECT count(*) FROM option_bars WHERE venue IS NULL").fetchone()[0]
    print(f"  backfilled venue/product; {n} rows still unset")
    for r in con.execute("""SELECT venue, product, count(*), min(expiry), max(expiry)
                            FROM option_bars GROUP BY 1,2 ORDER BY 1,2""").fetchall():
        print(f"    {r[0]:6} {r[1]:4} {r[2]:>8,} rows  {r[3]} -> {r[4]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--migrate", action="store_true")
    args = ap.parse_args()
    con = duckdb.connect(str(CN))
    if args.init:
        init(con)
    if args.migrate:
        migrate(con)
    con.close()


if __name__ == "__main__":
    main()
