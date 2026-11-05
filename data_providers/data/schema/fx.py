"""Source-separated dated FX observations, preserving unknown event clocks."""
DDL = """CREATE TABLE IF NOT EXISTS fx_observations (
    source VARCHAR, base_currency VARCHAR, quote_currency VARCHAR,
    vendor_session_date DATE, rate DOUBLE, price_basis VARCHAR,
    event_ts TIMESTAMPTZ, available_at TIMESTAMPTZ, captured_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ, receipt_sha256 VARCHAR, raw_json JSON,
    PRIMARY KEY(source,base_currency,quote_currency,vendor_session_date,receipt_sha256))"""


def ensure(con):
    con.execute(DDL)
