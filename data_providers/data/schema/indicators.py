"""Source/method-qualified reusable daily indicators; unknown availability stays NULL."""
DDL = '''CREATE TABLE IF NOT EXISTS daily_indicators (
    market VARCHAR,venue VARCHAR,instrument_id VARCHAR,symbol VARCHAR,contract_id VARCHAR,
    day DATE,source VARCHAR,method_id VARCHAR,field_id VARCHAR,
    value DOUBLE CHECK(value IS NULL OR isfinite(value)),units VARCHAR,
    available_at TIMESTAMPTZ,calculated_at TIMESTAMPTZ,input_release VARCHAR,
    quality VARCHAR,lineage JSON,
    PRIMARY KEY(market,venue,instrument_id,day,source,method_id,field_id))'''


def ensure(con):
    con.execute(DDL)
