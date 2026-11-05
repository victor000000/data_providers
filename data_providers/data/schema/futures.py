"""US derivatives use contract IDs and venues, never equity ticker identities."""
DDL = {
    'us_futures_iv': '''source VARCHAR, venue VARCHAR, root VARCHAR,
        series_id VARCHAR CHECK(starts_with(series_id,venue||':'||root||':')),
        day DATE, atm_iv DOUBLE NOT NULL CHECK(isfinite(atm_iv) AND atm_iv>0),
        tenor_days DOUBLE NOT NULL CHECK(tenor_days>0), moneyness_method VARCHAR NOT NULL,
        roll_method VARCHAR NOT NULL, model VARCHAR NOT NULL, method_version VARCHAR NOT NULL,
        underlying_contract_id VARCHAR, input_contracts JSON NOT NULL,
        available_at TIMESTAMPTZ, retrieved_at TIMESTAMPTZ, receipt_reference VARCHAR NOT NULL,
        PRIMARY KEY(source,venue,root,series_id,day)''',
    'us_futures_option_coverage': '''source VARCHAR, venue VARCHAR, root VARCHAR, day DATE,
        status VARCHAR, iv_sessions BIGINT, confirmed_contracts BIGINT, chain_rows BIGINT,
        option_bar_rows BIGINT, settlement_rows BIGINT, last_option_day DATE,
        volume_unit VARCHAR CHECK(volume_unit='contracts'), details JSON, checked_utc TIMESTAMPTZ,
        PRIMARY KEY(source,venue,root,day)''',
    'us_futures_products': '''source VARCHAR, product_code VARCHAR, venue VARCHAR, day DATE,
        name VARCHAR, details JSON, PRIMARY KEY(source,product_code,venue,day)''',
    'us_futures_contracts': '''source VARCHAR, contract_id VARCHAR, root VARCHAR, venue VARCHAR,
        sec_type VARCHAR, local_symbol VARCHAR, expiry DATE, strike DOUBLE, call_put VARCHAR,
        multiplier VARCHAR, currency VARCHAR, underlying_id BIGINT, captured_utc TIMESTAMP,
        details JSON, PRIMARY KEY(source,contract_id)''',
    'us_futures_daily': '''source VARCHAR, contract_id VARCHAR, root VARCHAR, venue VARCHAR,
        day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, settle DOUBLE,
        volume DOUBLE, oi DOUBLE, window_start_utc TIMESTAMP, PRIMARY KEY(source,contract_id,day)''',
    'us_futures_option_chains': '''source VARCHAR, root VARCHAR, venue VARCHAR, underlying_id BIGINT,
        trading_class VARCHAR, multiplier VARCHAR, day DATE, expirations JSON, strikes JSON,
        captured_utc TIMESTAMP, PRIMARY KEY(source,venue,underlying_id,trading_class,day)''',
    'us_futures_option_daily': '''source VARCHAR, contract_id VARCHAR, root VARCHAR, venue VARCHAR,
        underlying_id BIGINT, expiry DATE, strike DOUBLE, call_put VARCHAR, day DATE,
        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
        PRIMARY KEY(source,contract_id,day)''',
    'us_futures_option_settlements': '''source VARCHAR, contract_id VARCHAR, root VARCHAR, venue VARCHAR,
        underlying_contract_id VARCHAR, expiry DATE, strike DOUBLE, call_put VARCHAR,
        day DATE, settle DOUBLE, volume DOUBLE, oi DOUBLE,
        PRIMARY KEY(source,contract_id,day)''',
    'futures_ingestion_checks': '''run_id VARCHAR, market VARCHAR, venue VARCHAR, product VARCHAR,
        dataset VARCHAR, status VARCHAR, rows BIGINT, detail VARCHAR, checked_utc TIMESTAMP''',
}


def ensure(con):
    for table, columns in DDL.items():
        con.execute(f'CREATE TABLE IF NOT EXISTS {table} ({columns})')


CN_QUOTE_DDL = """CREATE TABLE IF NOT EXISTS option_quotes (
    source VARCHAR, venue VARCHAR, product VARCHAR, contract VARCHAR,
    underlying_contract VARCHAR, security_type VARCHAR, strike DOUBLE, call_put VARCHAR,
    ts_utc TIMESTAMP, last DOUBLE, bid DOUBLE, ask DOUBLE, oi DOUBLE, raw_json JSON,
    PRIMARY KEY(source,venue,contract,ts_utc))"""

CN_OPTION_CHECK_DDL = '''CREATE TABLE IF NOT EXISTS option_history_checks (
    source VARCHAR, venue VARCHAR, product VARCHAR, contract VARCHAR, target_day DATE,
    status VARCHAR, rows BIGINT, checked_utc TIMESTAMP, detail VARCHAR,
    PRIMARY KEY(source,venue,contract,target_day))'''
