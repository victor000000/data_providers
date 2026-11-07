# BRAIN PV/options delivery

This page preserves dated delivery/method evidence. For current acquisition scope,
see requirements (private path `docs/requirements/README.md`; [access boundaries](EVIDENCE.md#private-evidence)); for live status, see
[Operations](OPERATIONS.md). Counts and “current” results below refer to their
recorded run, not a continuously refreshed coverage claim.

The canonical calculation graph now also delivers **32,749 non-NULL US/CN PV
values** (33,729 rows with explicit warmup/missing-input cases) through
`daily_indicators`. Its three documented local methods and per-market historical
scope are in [Daily indicators](DAILY_INDICATORS.md). These calculated fields
are distinct from the primitive mapping/sample inventory below and do not claim
exact BRAIN equivalence or complete market coverage.

## Scope correction — November 7, 2026

Any older wording below describing HKG features as pending is historical only.
HK/HKG and other non-US/non-mainland-CN listings are excluded from acquisition,
not future delivery gaps. Primitive mappings and samples do not establish current
market coverage or proprietary BRAIN formula equivalence. No stored evidence or
historical receipt was changed in this correction.

## Current union and actual option sample — 2026-10-18

The authenticated supplement extends the mapping inventory to **20,287 keys**:
11,794 cached keys confirmed in refreshed scopes, 106 new keys and 8,387 cached
keys retained with unconfirmed freshness. Cached and fresh description sets
remain separate, with a combined distinct set used for conservative mapping.
591 description-set differences are scoped comparison evidence, not proof of
changed formulas. `fresh_availability.parquet` preserves each fresh definition's
region/universe/delay, retrieval time, raw JSON and relative source receipt path;
`cached_fields.parquet` retains original cached scope summaries and unknown
retrieval freshness. Both input bundle manifests are hashed in the run report.

Latest verified run:
`runtime/var/maintenance/brain-inventory-20261018T014957Z-4b6891bf/`.
It contains **60 PV observations plus 200 option field values** from 100 actual
source contract-day rows. The two additional mapped fields are
`option:option2:option_contract_strike_price` and
`option:option2:option_contract_trading_volume`. They are long-form VECTOR
records keyed by field/source-qualified contract/session, retaining vendor
symbol, underlying, expiry, strike and put/call. Values are verified against
the pinned source's exact contract identity and session. No arrays are aligned
by position or aggregated into a fabricated underlying indicator.

The seven observed mappings describe source-native primitives; BRAIN value and
universe equivalence remain unverified. Current counts are seven `observed`,
20,279 `metadata_only` and one `ambiguous_definition`. Option adjustment and
historical availability remain unknown. A strike observation is contract
metadata observed alongside a bar, not another execution. The contract-day
sample is deliberately capped at 100 and does not assert complete daily chains.

```bash
./scripts/native.sh python -m data_providers.jobs.audit.brain_inventory \
  --catalog docs/requirements/brain_pv_options_20261018/catalog \
  --fresh-catalog docs/requirements/brain_pv_options_20261018/fresh \
  --with-option-sample --symbols SPY --start 2026-10-01 --end 2026-10-16 \
  --release 20261018T010604Z_e4064ee0
```

The optional `option_sample_values` DuckDB table and Parquet file expose
`field_key`, `contract_id`, `symbol`, `underlying`, `expiry`, `strike`,
`call_put`, `session_date`, `value`, `units`, `source`, `available_at`,
`retrieved_at`, `source_release`, `method_version` and `quality`.
The native MarketData input remains `us_option_bars`:

```python
m.read("us_option_bars", symbols=["SPY"], start="2026-10-01", end="2026-10-16", limit=100)
```

Quote prices are not substituted with bar closes. Constant-tenor IV, HKG
intraday features and proprietary outputs remain explicitly pending. Existing
download workers are preserved, and no broad ingestion or database backup copy
is performed by this audit.

## Initial cached-catalog PV sample

The requirement is preserved in
the complete handoff (private path `docs/requirements/BRAIN_PV_OPTIONS_20261018.md`; [access boundaries](EVIDENCE.md#private-evidence)). Its 20,181 field
keys describe desired data; metadata is not historical value coverage.

The provider audit `data_providers.jobs.audit.brain_inventory` verifies all catalog
manifest checksums, preserves every field's descriptions/type variants/scopes, and
produces one mapping row per exact category/dataset/field key. Unknown dependencies,
units and methods remain unknown rather than inferred from identifiers.

The first bounded audit used release `20261018T010604Z_e4064ee0`, SPY, and
2026-10-01 through 2026-10-16. It produced **60 actual observations across 12
sessions and five source-native daily PV fields**: raw open/high/low/close and
volume. Five mappings are `observed`, 20,175 remain `metadata_only`, and one is
`ambiguous_definition` (`opt6_30div`: saved description says 20 days).
`observed` means a local provider value exists; BRAIN equivalence is explicitly
unverified. Neither this sample nor this inventory establishes all-market coverage.

The audit is reproducible on the host through the native environment:

```bash
./scripts/native.sh python -m data_providers.jobs.audit.brain_inventory \
  --catalog docs/requirements/brain_pv_options_20261018/catalog \
  --symbols SPY --start 2026-10-01 --end 2026-10-16 \
  --release 20261018T010604Z_e4064ee0
```

Each new `runtime/var/maintenance/brain-inventory-*/` folder contains
`inventory.duckdb`, `fields.parquet`, `mapping.parquet`, `sample_values.parquet`
and `report.json`. Only the final report signals verified completion; unfinished
folders are retained. The report includes catalog hashes, source release and
partition receipts, sample bounds, status counts and output checksums. Exact
values are compared against the pinned source and both directions of each
DuckDB/Parquet export are checked. This audit reads existing observations and
does not download or alter market data or replace the lake's current release.

Actual consumer read API:

```python
from data_providers import MarketData

m = MarketData(backend="parquet", release="20261018T010604Z_e4064ee0")
values = m.read("us_prices", symbols=["SPY"], start="2026-10-01", end="2026-10-16",
                columns=["symbol", "date", "open", "high", "low", "close", "volume"])
coverage = m.status("us_prices")
```

For the audit artifacts, substitute the exact run folder printed by the command:

```sql
SELECT f.field_key, f.descriptions_json, m.status, m.equivalence, m.next_action
FROM read_parquet('/absolute/run/fields.parquet') f
JOIN read_parquet('/absolute/run/mapping.parquet') m USING (field_key);

SELECT field_key, symbol, session_date, value, source, price_convention,
       method_version, available_at, source_release, quality
FROM read_parquet('/absolute/run/sample_values.parquet')
ORDER BY field_key, symbol, session_date;
```

The sample preserves source dates and unadjusted columns. Historical availability
and original retrieval timestamps remain NULL; an audit timestamp is neither.
Price units remain source quote currency, without inventing currency metadata.
The report pins the original lake partitions and their checksums; source vendor
raw-response provenance is not newly established by copying an existing release.

Remaining work includes historical option-vector alignment, actual quote access,
30/45/90-day IV methods and inputs, HKG intraday observations and session rules,
and proprietary definitions. The option1 stock-close VECTOR is not mapped to a
scalar option premium. Generic IBKR IV is not assigned to constant-tenor call IV.
OHLC averages are not substituted for VWAP, and ambiguous returns are not computed
before resolving adjustment/total-return conventions. No option, HKG or model
output values are claimed by this initial PV sample.

Requirement monitoring is active in the current provider chat every 30 minutes.
It inspects dte_21_screener, qc_work and brain_work requirements and chat progress,
preserves active maintenance workers, and reports meaningful changes only.
The initial requirement-file hash receipt is
`.local/consumer-requirements-observed.json`; durable run status is kept under
`runtime/var/maintenance/consumer-monitor/`.
