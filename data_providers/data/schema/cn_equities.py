"""Mainland cash-market bars keep adjustment variants and source units explicit."""
DDL = '''CREATE TABLE IF NOT EXISTS equity_daily (
    source VARCHAR,venue VARCHAR,instrument_id VARCHAR,symbol VARCHAR,asset_class VARCHAR,
    day DATE,adjustment VARCHAR,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,
    volume_native DOUBLE,volume_unit VARCHAR,turnover_native DOUBLE,currency VARCHAR,
    available_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ,receipt_reference VARCHAR,
    PRIMARY KEY(source,venue,instrument_id,day,adjustment))'''


def ensure(con):
    con.execute(DDL)
