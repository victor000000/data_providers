"""Point-in-time research observations; no fabricated timestamps or contract terms."""
DDL = {
    'index_option_settlements': """CREATE TABLE IF NOT EXISTS index_option_settlements (
        source VARCHAR, trading_symbol VARCHAR, underlying VARCHAR, expiry DATE,
        settlement_value DECIMAL(20,8), published_at TIMESTAMPTZ,
        available_at TIMESTAMPTZ, received_at TIMESTAMPTZ,
        source_url VARCHAR, receipt_sha256 VARCHAR, raw_json JSON,
        PRIMARY KEY(source,trading_symbol,expiry,receipt_sha256))""",
    'option_intraday_greeks': """CREATE TABLE IF NOT EXISTS option_intraday_greeks (
        source VARCHAR, contract_id VARCHAR, underlying VARCHAR, session_date DATE,
        interval_ts TIMESTAMPTZ, frequency_seconds INTEGER, model_version VARCHAR,
        quote_ts TIMESTAMPTZ, available_at TIMESTAMPTZ, received_at TIMESTAMPTZ,
        underlying_ts TIMESTAMPTZ, underlying_price DOUBLE, bid DOUBLE, ask DOUBLE,
        iv DOUBLE, delta DOUBLE, gamma DOUBLE, theta DOUBLE, vega DOUBLE, rho DOUBLE,
        iv_error DOUBLE, model VARCHAR, units_json JSON, quality VARCHAR,
        receipt_sha256 VARCHAR, raw_json JSON,
        PRIMARY KEY(source,contract_id,interval_ts,frequency_seconds,model_version))""",
    'option_reference_bars': """CREATE TABLE IF NOT EXISTS option_reference_bars (
        source VARCHAR, contract_id VARCHAR, underlying VARCHAR, expiry DATE,
        strike DOUBLE, call_put VARCHAR, multiplier DOUBLE, currency VARCHAR,
        session_date DATE, interval_start TIMESTAMPTZ, interval_end TIMESTAMPTZ,
        nominal_seconds INTEGER, interval_end_basis VARCHAR, price_basis VARCHAR,
        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
        quote_event_ts TIMESTAMPTZ, available_at TIMESTAMPTZ, captured_at TIMESTAMPTZ,
        receipt_sha256 VARCHAR, raw_json JSON,
        PRIMARY KEY(source,contract_id,interval_start,nominal_seconds,receipt_sha256))""",
    'underlying_research_daily': """CREATE TABLE IF NOT EXISTS underlying_research_daily (
        source VARCHAR, symbol VARCHAR, session_date DATE, observation_ts TIMESTAMPTZ,
        available_at TIMESTAMPTZ, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        volume BIGINT, price_basis VARCHAR, session_scope VARCHAR, volume_unit VARCHAR,
        receipt_sha256 VARCHAR, raw_json JSON,
        PRIMARY KEY(source,symbol,session_date))""",
    'option_nbbo': """CREATE TABLE IF NOT EXISTS option_nbbo (
        source VARCHAR, contract_id VARCHAR, underlying VARCHAR, session_date DATE,
        interval_ts TIMESTAMPTZ, quote_ts TIMESTAMPTZ, available_at TIMESTAMPTZ,
        bid DOUBLE, ask DOUBLE, bid_size BIGINT, ask_size BIGINT,
        bid_exchange VARCHAR, ask_exchange VARCHAR, bid_condition VARCHAR, ask_condition VARCHAR,
        quote_age_ms BIGINT, timestamp_kind VARCHAR, source_timezone VARCHAR,
        quality VARCHAR, receipt_sha256 VARCHAR,
        PRIMARY KEY(source, contract_id, interval_ts))""",
    'option_contract_history': """CREATE TABLE IF NOT EXISTS option_contract_history (
        source VARCHAR, contract_id VARCHAR, underlying VARCHAR, session_date DATE,
        expiry DATE, strike DECIMAL(18,6), call_put VARCHAR, multiplier DOUBLE,
        underlying_contract_id VARCHAR, exchange VARCHAR, exercise_style VARCHAR,
        settlement_type VARCHAR, series_type VARCHAR, deliverables VARCHAR,
        available_at TIMESTAMPTZ, retrieved_at TIMESTAMPTZ, metadata_status VARCHAR,
        receipt_sha256 VARCHAR, raw_json JSON,
        PRIMARY KEY(source, contract_id, session_date))""",
    'option_research_observations': """CREATE TABLE IF NOT EXISTS option_research_observations (
        source VARCHAR, contract_id VARCHAR, underlying VARCHAR, session_date DATE,
        kind VARCHAR, observation_ts TIMESTAMPTZ, available_at TIMESTAMPTZ,
        open_interest BIGINT, iv DOUBLE, delta DOUBLE, gamma DOUBLE, theta DOUBLE, vega DOUBLE,
        model VARCHAR, inputs_json JSON, receipt_sha256 VARCHAR,
        PRIMARY KEY(source,contract_id,session_date,kind))""",
    'underlying_research_bars': """CREATE TABLE IF NOT EXISTS underlying_research_bars (
        source VARCHAR, symbol VARCHAR, session_date DATE, interval_start TIMESTAMPTZ,
        interval_end TIMESTAMPTZ, available_at TIMESTAMPTZ,
        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
        price_basis VARCHAR, volume_unit VARCHAR, source_timezone VARCHAR, receipt_sha256 VARCHAR,
        PRIMARY KEY(source,symbol,interval_start))""",
    'research_coverage': """CREATE TABLE IF NOT EXISTS research_coverage (
        run_id VARCHAR, source VARCHAR, dataset VARCHAR, symbol VARCHAR, contract_id VARCHAR,
        session_date DATE, status VARCHAR, rows BIGINT, reason VARCHAR,
        receipt_sha256 VARCHAR, checked_at TIMESTAMPTZ,
        PRIMARY KEY(run_id,source,dataset,symbol,contract_id,session_date))""",
}


def ensure(con):
    for ddl in DDL.values():
        con.execute(ddl)
