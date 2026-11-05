"""Risk snapshots use the full option contract, including expiry, as their key."""

KEY = "live_timestamp_utc, account, underlying, strike, call_put, expiry"
COLUMNS = """
    snapshot_timestamp_utc TIMESTAMP NOT NULL, live_timestamp_utc TIMESTAMP NOT NULL,
    account TEXT NOT NULL, broker TEXT, underlying TEXT NOT NULL,
    strike DOUBLE, call_put TEXT, qty INTEGER, entry_price DOUBLE,
    snapshot_mark DOUBLE, snapshot_spot DOUBLE, snapshot_pnl_pct DOUBLE,
    snapshot_pnl_dollars DOUBLE, live_mark DOUBLE, live_spot DOUBLE,
    live_pnl_pct DOUBLE, live_pnl_dollars DOUBLE, target_50_price DOUBLE,
    distance_to_target DOUBLE, session_type TEXT, price_source TEXT, expiry DATE,
    dte INTEGER, delta_units DOUBLE, theta_day DOUBLE, vega_pt DOUBLE,
    spot_source TEXT, mark_source TEXT, snap_iv DOUBLE
"""


def ensure(con):
    """Create the table, or migrate its old key atomically without losing history.

    DuckDB cannot alter a primary key in place. A failed copy (including legacy
    rows with unknown expiries) rolls back, leaving the original table intact.
    """
    con.execute(f"CREATE TABLE IF NOT EXISTS risk_snapshots ({COLUMNS}, PRIMARY KEY ({KEY}))")
    for col, typ in (("spot_source", "TEXT"), ("mark_source", "TEXT"), ("snap_iv", "DOUBLE")):
        con.execute(f"ALTER TABLE risk_snapshots ADD COLUMN IF NOT EXISTS {col} {typ}")
    keys = con.execute("""
        SELECT constraint_column_names FROM duckdb_constraints()
        WHERE database_name = current_database() AND schema_name = current_schema()
          AND table_name = 'risk_snapshots' AND constraint_type = 'PRIMARY KEY'
    """).fetchall()
    if keys == [([k.strip() for k in KEY.split(",")],)]:
        return
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(f"CREATE TABLE risk_snapshots_with_expiry ({COLUMNS}, PRIMARY KEY ({KEY}))")
        con.execute("INSERT INTO risk_snapshots_with_expiry BY NAME SELECT * FROM risk_snapshots")
        con.execute("DROP TABLE risk_snapshots")
        con.execute("ALTER TABLE risk_snapshots_with_expiry RENAME TO risk_snapshots")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
