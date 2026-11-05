"""Broker-reported option entry costs, separate from the legacy holdings schema."""


def write(con, rows):
    con.execute("""CREATE TABLE IF NOT EXISTS broker_position_costs (
        as_of TIMESTAMP, account TEXT, broker TEXT, underlying TEXT, expiry DATE,
        strike DOUBLE, call_put TEXT, qty DOUBLE, avg_price DOUBLE, source TEXT,
        PRIMARY KEY(as_of,account,broker,underlying,expiry,strike,call_put))""")
    if rows:
        con.executemany("INSERT OR REPLACE INTO broker_position_costs VALUES "
                        "(?,?,?,?,?,?,?,?,?,?)", rows)


def current_prices(con):
    if not con.execute("SELECT count(*) FROM duckdb_tables() "
                       "WHERE table_name='broker_position_costs'").fetchone()[0]:
        return {}
    rows = con.execute("""
        WITH costs AS (
          SELECT *,row_number() OVER (
            PARTITION BY account,broker,underlying,expiry,strike,call_put
            ORDER BY as_of DESC) AS rn FROM broker_position_costs)
        SELECT b.account,b.broker,b.underlying,b.expiry,b.strike,b.call_put,c.avg_price
          FROM broker_positions b JOIN costs c
            ON c.account=b.account AND c.broker=b.broker AND c.underlying=b.underlying
           AND c.expiry=b.expiry AND c.strike=b.strike AND c.call_put=b.call_put
         WHERE c.rn=1 AND c.qty=b.qty AND c.avg_price>0 AND isfinite(c.avg_price)
           AND c.as_of<=b.as_of
           AND b.as_of=(SELECT max(b2.as_of) FROM broker_positions b2
                        WHERE b2.account=b.account AND b2.broker=b.broker)
           AND NOT EXISTS (SELECT 1 FROM fills f WHERE f.account=b.account
             AND f.underlying=b.underlying AND f.expiry=b.expiry AND f.strike=b.strike
             AND f.call_put=b.call_put AND f.ts_utc>c.as_of AND f.ts_utc<=b.as_of)
        """).fetchall()
    return {(a,b,u,e,float(k),cp):px for a,b,u,e,k,cp,px in rows}
