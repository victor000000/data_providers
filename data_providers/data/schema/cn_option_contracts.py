"""Dated native contract-master observations; not historical trade bars."""

DDL = '''CREATE TABLE IF NOT EXISTS option_contract_snapshots (
    venue VARCHAR NOT NULL, exchange_contract_id VARCHAR NOT NULL,
    contract_code VARCHAR NOT NULL, underlying VARCHAR NOT NULL,
    report_date DATE NOT NULL, expiry DATE NOT NULL,
    last_trade_date DATE, exercise_date DATE, delivery_date DATE,
    call_put VARCHAR NOT NULL, strike DOUBLE NOT NULL, multiplier INTEGER NOT NULL,
    original_contract_code VARCHAR, original_strike DOUBLE, original_multiplier INTEGER,
    adjustment_flag VARCHAR, previous_settlement DOUBLE,
    previous_settlement_date DATE, open_interest DOUBLE,
    source VARCHAR NOT NULL, source_receipt VARCHAR NOT NULL, raw_sha256 VARCHAR NOT NULL,
    received_at TIMESTAMP NOT NULL, source_event_utc TIMESTAMP, available_at TIMESTAMP,
    native_fields JSON NOT NULL, provenance_status VARCHAR NOT NULL,
    PRIMARY KEY(venue, exchange_contract_id, report_date, source)
)'''


def ensure(con):
    con.execute(DDL)
