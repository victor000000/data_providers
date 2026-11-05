# Logical dataset reference

Reviewed against [catalog.py](../data_providers/catalog.py) on 2026-10-31. These
are logical SDK/CLI names, not promises of populated or complete source coverage.
For exact fields/types use `schema`; for declared keys and physical partitions
inspect the pinned release manifest or the owning schema module.

```bash
./scripts/native.sh data-providers datasets
./scripts/native.sh data-providers --private datasets
./scripts/native.sh data-providers schema us_option_quotes
./scripts/native.sh data-providers status us_option_quotes
```

## Physical stores

All paths below are relative to the effective runtime, normally
`/home/vic/data_providers/runtime`.

| Store alias | Ingestion file | Main role |
| --- | --- | --- |
| `history` | `market_history.duckdb` | US daily/minute/options/futures, research facts, provenance and indicators |
| `universe` | `market_data/yahoo.duckdb` | US price warehouse, symbols and index membership |
| `cn` | `cn_market.duckdb` | Venue-qualified CN data and product inventory |
| `reference` | `reference.duckdb` | Instruments and option contract reference |
| `trades` | `trades.duckdb` | Private fills, accounts, raw export rows and broker snapshots |

The catalog exposes selected tables. Publication also captures store tables/views
that do not have a logical SDK name. A catalog entry alone does not initialize a
table; reads fail visibly if the selected store/release lacks it.

## Catalog mapping

`Symbol field` is the field targeted by SDK `symbols` or CLI `--symbol`. It is
not universally a stock ticker. `Time field` controls start/end and status bounds;
`—` means the generic filter is unsupported. `Private` requires `private=True`
or CLI `--private` and a backend/release containing the table.

### History store

| Dataset | Physical table | Time field | Symbol field | Private |
| --- | --- | --- | --- | --- |
| `iv_observations_v2` | `iv_observations_v2` | `day` | `instrument_id` | no |
| `data_source_registry` | `data_source_registry` | — | `datasource_tag` | no |
| `source_receipts` | `source_receipts` | `received_at` | `datasource_tag` | no |
| `us_index_option_settlements` | `index_option_settlements` | `expiry` | `underlying` | no |
| `fx_observations` | `fx_observations` | `vendor_session_date` | `base_currency` | no |
| `us_option_reference_bars` | `option_reference_bars` | `interval_start` | `underlying` | no |
| `capitalization_observations` | `capitalization_observations` | `retrieved_at` | `symbol` | no |
| `option_listing_observations` | `option_listing_observations` | `retrieved_at` | `underlying` | no |
| `daily_indicators` | `daily_indicators` | `day` | `instrument_id` | no |
| `us_futures_iv` | `us_futures_iv` | `day` | `root` | no |
| `us_futures_option_coverage` | `us_futures_option_coverage` | `day` | `root` | no |
| `us_underlying_research_daily` | `underlying_research_daily` | `session_date` | `symbol` | no |
| `us_option_intraday_greeks` | `option_intraday_greeks` | `interval_ts` | `underlying` | no |
| `us_option_quotes` | `option_nbbo` | `interval_ts` | `underlying` | no |
| `us_option_contract_history` | `option_contract_history` | `session_date` | `underlying` | no |
| `us_option_research_observations` | `option_research_observations` | `session_date` | `underlying` | no |
| `us_underlying_research_bars` | `underlying_research_bars` | `interval_end` | `symbol` | no |
| `research_coverage` | `research_coverage` | `session_date` | `symbol` | no |
| `us_futures_products` | `us_futures_products` | `day` | `product_code` | no |
| `us_futures_option_settlements` | `us_futures_option_settlements` | `day` | `root` | no |
| `us_futures_contracts` | `us_futures_contracts` | `captured_utc` | `root` | no |
| `us_futures_daily` | `us_futures_daily` | `day` | `root` | no |
| `us_futures_option_chains` | `us_futures_option_chains` | `day` | `root` | no |
| `us_futures_option_daily` | `us_futures_option_daily` | `day` | `root` | no |
| `futures_ingestion_checks` | `futures_ingestion_checks` | `checked_utc` | `product` | no |
| `us_daily` | `underlying_daily` | `day` | `symbol` | no |
| `us_minutes` | `underlying_minutes` | `ts_utc` | `symbol` | no |
| `us_option_bars` | `option_bars` | `day` | `underlying` | no |
| `us_option_minutes` | `option_minutes` | `ts_utc` | `underlying` | no |
| `us_iv` | `iv_history` | `day` | `underlying` | no |
| `us_option_greeks` | `option_greeks_daily` | `day` | `underlying` | no |
| `us_option_oi` | `option_oi_daily` | `day` | `underlying` | no |
| `us_option_flow` | `option_flow_daily` | `day` | `underlying` | no |
| `fundamentals` | `fundamentals_snapshot` | `day` | `symbol` | no |

### Universe store

| Dataset | Physical table | Time field | Symbol field | Private |
| --- | --- | --- | --- | --- |
| `us_prices` | `prices` | `date` | `symbol` | no |
| `us_symbols` | `symbols` | — | `symbol` | no |
| `index_membership` | `index_membership` | — | `symbol` | no |

### Cn store

| Dataset | Physical table | Time field | Symbol field | Private |
| --- | --- | --- | --- | --- |
| `cn_optionable_underlyings` | `optionable_underlying_inventory` | — | `canonical` | no |
| `cn_equity_daily` | `equity_daily` | `day` | `instrument_id` | no |
| `cn_option_history_checks` | `option_history_checks` | `checked_utc` | `product` | no |
| `cn_option_quotes` | `option_quotes` | `ts_utc` | `product` | no |
| `cn_daily` | `futures_daily` | `day` | `product` | no |
| `cn_option_bars` | `option_bars` | `day` | `product` | no |
| `cn_iv` | `iv_history` | `day` | `underlying` | no |
| `cn_products` | `cn_product_map` | — | `canonical` | no |

### Reference store

| Dataset | Physical table | Time field | Symbol field | Private |
| --- | --- | --- | --- | --- |
| `instruments` | `instruments` | — | `underlying` | no |
| `option_contracts` | `option_contracts` | — | `underlying` | no |

### Trades store

| Dataset | Physical table | Time field | Symbol field | Private |
| --- | --- | --- | --- | --- |
| `broker_snapshot_receipts` | `broker_snapshot_receipts` | `source_local_clock` | — | yes |
| `broker_snapshot_positions` | `broker_snapshot_positions` | — | `underlying` | yes |
| `fills` | `fills` | `ts_utc` | `underlying` | yes |
| `accounts` | `account_daily` | `day` | — | yes |
| `broker_export_rows` | `broker_export_rows` | — | — | yes |

## Choosing a dataset

| Need | Starting datasets and cautions |
| --- | --- |
| US raw/adjusted price history | `us_prices`; choose raw OHLC or `adj_*` explicitly |
| Source-specific US daily/minute bars | `us_daily`, `us_minutes`; inspect `source` and timestamps |
| Option trade/daily bars | `us_option_bars`, `us_option_minutes`; these do not substitute for NBBO |
| Historical sampled option book | `us_option_quotes`; select source, contract and interval, retaining quote age and unknown availability |
| Native intraday vendor Greeks | `us_option_intraday_greeks`; retain model version/units and invalid-input flags |
| Method-separated IV | `iv_observations_v2`, `data_source_registry`, `source_receipts`; do not splice independent methods |
| Actual US futures/FOPs | `us_futures_contracts`, `us_futures_daily`, `us_futures_option_daily`; join stable contract IDs |
| Official settlement values | `us_index_option_settlements` or `us_futures_option_settlements`; distinct products and semantics |
| CN daily/options | `cn_daily`, `cn_equity_daily`, `cn_option_bars`, `cn_option_quotes`; identities and symbol filters differ |
| Executions | Private `fills`; deduplicate overlapping broker exports |
| Dated Schwab position exports | Private `broker_snapshot_receipts` + `broker_snapshot_positions`; join receipt hash/account |

## Filter and interpretation details

- Date/time bounds apply to the catalog time field, not an arbitrary event or
  publication clock. `source_receipts` selects by retrieval time; settlements by
  expiry; snapshot receipts by a source-local clock label. That last field must
  not be silently interpreted as UTC without a known timezone.
- `cn_daily` selects product codes such as `MA`; add `venue="CZCE"` where supported.
  `cn_products` and `cn_optionable_underlyings` select canonical keys such as
  `CN:CZCE:MA`; `cn_equity_daily` selects its instrument ID.
- SDK `contract_ids=[...]` requires a physical `contract_id` column. HTTP uses
  repeated `contract_id`. The CLI has no equivalent option.
- Generic source filtering requires a physical `source` field, and venue filtering
  requires `venue`. Consult `schema` before applying either to a new dataset.
- `symbols=[]` selects nothing; omitted symbols select all. The SDK rejects a
  string in place of a list. Empty contract-ID lists also select nothing.
- SDK `columns=[...]` projects existing fields. `read_tagged` retains attribution
  metadata even when projecting data columns. Plain `read` and `export` retain
  their existing schemas; use [provenance](DATA_PROVENANCE.md) for attribution.
- Reads are deterministic bounded selections, not latest-row queries. A limited
  response or table-wide status range does not establish per-contract coverage.
- Catalog privacy is an accidental-disclosure guard, not an operating-system ACL.
  Public-only releases cannot supply private tables even when private SDK access
  is requested.

`broker_snapshot_positions` has no generic time filter. Join it to receipts to
select a dated snapshot. Unknown `source_timezone` / `source_event_utc` stay null;
`ingested_at` is processing time. Parsed nonzero option rows do not establish a
complete account or prove omitted positions are flat.

## Schema, partitions and evolution

The publication manifest records logical types, primary keys, counts, checksums,
source descriptors and exact files. It is the release-specific schema authority.
The catalog records filter routing, not full schemas or every key constraint.

Most temporal tables use yearly partitions. `option_nbbo` and
`option_intraday_greeks` use UTC-day partitions; `iv_observations_v2` uses source
and calendar month. Undated/non-temporal fields use an unpartitioned representation;
null temporal values remain separate and empty tables retain typed files. Readers
follow manifest paths rather than assuming a directory layout or globbing files.

Earlier releases may use older layouts. They remain immutable and readable through
their own manifests. Later manifests may reference earlier files; never delete a
snapshot directory by age. JSON and 128-bit integers have lossless logical-type
restoration through lake readers. See [Architecture](ARCHITECTURE.md) and
[Data access](DATA_ACCESS.md) for publication and export behavior.

When adding or changing a dataset, update the owning schema, catalog, ingestion
and consumer contract together. Regenerate/check this mapping against the catalog,
run the relevant isolated tests, and record any breaking migration explicitly.
