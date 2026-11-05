"""Versioned logical dataset names; consumers never need physical file names."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    file: str
    table: str
    time: str | None
    symbol: str | None
    private: bool = False


CATALOG = {
    "cn_option_contract_snapshots": Dataset("cn_market.duckdb", "option_contract_snapshots", "report_date", "underlying"),
    "cn_optionable_underlyings": Dataset("cn_market.duckdb", "optionable_underlying_inventory", None, "canonical"),
    "iv_observations_v2": Dataset("market_history.duckdb", "iv_observations_v2", "day", "instrument_id"),
    "data_source_registry": Dataset("market_history.duckdb", "data_source_registry", None, "datasource_tag"),
    "source_receipts": Dataset("market_history.duckdb", "source_receipts", "received_at", "datasource_tag"),
    "us_index_option_settlements": Dataset("market_history.duckdb", "index_option_settlements", "expiry", "underlying"),
    "broker_snapshot_receipts": Dataset("trades.duckdb", "broker_snapshot_receipts", "source_local_clock", None, True),
    "broker_snapshot_positions": Dataset("trades.duckdb", "broker_snapshot_positions", None, "underlying", True),
    "fx_observations": Dataset("market_history.duckdb", "fx_observations", "vendor_session_date", "base_currency"),
    "us_option_reference_bars": Dataset("market_history.duckdb", "option_reference_bars", "interval_start", "underlying"),
    "capitalization_observations": Dataset("market_history.duckdb", "capitalization_observations", "retrieved_at", "symbol"),
    "option_listing_observations": Dataset("market_history.duckdb", "option_listing_observations", "retrieved_at", "underlying"),
    "cn_equity_daily": Dataset("cn_market.duckdb", "equity_daily", "day", "instrument_id"),
    "daily_indicators": Dataset("market_history.duckdb", "daily_indicators", "day", "instrument_id"),
    "us_futures_iv": Dataset("market_history.duckdb", "us_futures_iv", "day", "root"),
    "us_futures_option_coverage": Dataset("market_history.duckdb", "us_futures_option_coverage", "day", "root"),
    "us_underlying_research_daily": Dataset("market_history.duckdb", "underlying_research_daily", "session_date", "symbol"),
    "us_option_intraday_greeks": Dataset("market_history.duckdb", "option_intraday_greeks", "interval_ts", "underlying"),
    "us_option_quotes": Dataset("market_history.duckdb", "option_nbbo", "interval_ts", "underlying"),
    "us_option_contract_history": Dataset("market_history.duckdb", "option_contract_history", "session_date", "underlying"),
    "us_option_research_observations": Dataset("market_history.duckdb", "option_research_observations", "session_date", "underlying"),
    "us_underlying_research_bars": Dataset("market_history.duckdb", "underlying_research_bars", "interval_end", "symbol"),
    "research_coverage": Dataset("market_history.duckdb", "research_coverage", "session_date", "symbol"),
    "us_futures_products": Dataset("market_history.duckdb", "us_futures_products", "day", "product_code"),
    "us_futures_option_settlements": Dataset("market_history.duckdb", "us_futures_option_settlements", "day", "root"),
    "us_futures_contracts": Dataset("market_history.duckdb", "us_futures_contracts", "captured_utc", "root"),
    "us_futures_daily": Dataset("market_history.duckdb", "us_futures_daily", "day", "root"),
    "us_futures_option_chains": Dataset("market_history.duckdb", "us_futures_option_chains", "day", "root"),
    "us_futures_option_daily": Dataset("market_history.duckdb", "us_futures_option_daily", "day", "root"),
    "futures_ingestion_checks": Dataset("market_history.duckdb", "futures_ingestion_checks", "checked_utc", "product"),
    "instruments": Dataset("reference.duckdb", "instruments", None, "underlying"),
    "option_contracts": Dataset("reference.duckdb", "option_contracts", None, "underlying"),
    "us_prices": Dataset("market_data/yahoo.duckdb", "prices", "date", "symbol"),
    "us_symbols": Dataset("market_data/yahoo.duckdb", "symbols", None, "symbol"),
    "index_membership": Dataset("market_data/yahoo.duckdb", "index_membership", None, "symbol"),
    "us_daily": Dataset("market_history.duckdb", "underlying_daily", "day", "symbol"),
    "us_minutes": Dataset("market_history.duckdb", "underlying_minutes", "ts_utc", "symbol"),
    "us_option_bars": Dataset("market_history.duckdb", "option_bars", "day", "underlying"),
    "us_option_minutes": Dataset("market_history.duckdb", "option_minutes", "ts_utc", "underlying"),
    "us_iv": Dataset("market_history.duckdb", "iv_history", "day", "underlying"),
    "us_option_greeks": Dataset("market_history.duckdb", "option_greeks_daily", "day", "underlying"),
    "us_option_oi": Dataset("market_history.duckdb", "option_oi_daily", "day", "underlying"),
    "us_option_flow": Dataset("market_history.duckdb", "option_flow_daily", "day", "underlying"),
    "fundamentals": Dataset("market_history.duckdb", "fundamentals_snapshot", "day", "symbol"),
    "cn_option_history_checks": Dataset("cn_market.duckdb", "option_history_checks", "checked_utc", "product"),
    "cn_option_quotes": Dataset("cn_market.duckdb", "option_quotes", "ts_utc", "product"),
    "cn_daily": Dataset("cn_market.duckdb", "futures_daily", "day", "product"),
    "cn_option_bars": Dataset("cn_market.duckdb", "option_bars", "day", "product"),
    "cn_iv": Dataset("cn_market.duckdb", "iv_history", "day", "underlying"),
    "cn_products": Dataset("cn_market.duckdb", "cn_product_map", None, "canonical"),
    "fills": Dataset("trades.duckdb", "fills", "ts_utc", "underlying", True),
    "accounts": Dataset("trades.duckdb", "account_daily", "day", None, True),
    "broker_export_rows": Dataset("trades.duckdb", "broker_export_rows", None, None, True),
}
