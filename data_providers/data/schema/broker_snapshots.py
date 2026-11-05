"""Export snapshots separate source clocks from import clocks and completeness."""
DDL = """
CREATE TABLE IF NOT EXISTS broker_snapshot_receipts (
 receipt_sha256 VARCHAR, account VARCHAR, broker VARCHAR, source VARCHAR,
 filename VARCHAR, source_local_clock TIMESTAMP, source_timezone VARCHAR,
 source_event_utc TIMESTAMPTZ, ingested_at TIMESTAMPTZ,
 complete_account BOOLEAN, omitted_means_flat BOOLEAN, position_scope VARCHAR,
 raw_content BLOB, PRIMARY KEY(receipt_sha256,account));
CREATE TABLE IF NOT EXISTS broker_snapshot_positions (
 receipt_sha256 VARCHAR, account VARCHAR, broker VARCHAR, underlying VARCHAR,
 sec_type VARCHAR, symbol VARCHAR, expiry DATE, strike DOUBLE, call_put VARCHAR,
 currency VARCHAR, qty DOUBLE,
 PRIMARY KEY(receipt_sha256,account,symbol,expiry,strike,call_put));
"""


def ensure(con):
    con.execute(DDL)
