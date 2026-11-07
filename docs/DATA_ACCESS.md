# Read and deliver data

```python
from data_providers import MarketData

data = MarketData()
rows = data.read("us_daily", symbols=["SPY"], start="2026-10-01",
                 end="2026-10-16", source="ibkr_rth_daily")
schema = data.schema("us_option_bars")
```

Run provider commands on the host from `/home/vic/data_providers` through
`scripts/native.sh`; see [Native environment](NATIVE.md). Consumer examples use the consumer’s own environment.

Logical dataset names are listed by `data-providers datasets`. The SDK pins the current published release when available, queries it read-only,
and closes connections after each request. Before the first publication it uses
existing read-only ingestion stores; reads never create missing databases. Date/time filters follow each catalogued time field. Market timestamps use UTC;
explicit source-local fields such as snapshot `source_local_clock` retain their
documented local semantics. Bounds
are inclusive; a date-only minute-series end includes the full UTC day.
Reads default to 10,000 rows and are capped at 100,000. Use exports for larger
selections. `symbols=[]` selects no rows; omitting symbols selects all symbols.

US and CN datasets are separate namespaces. In `cn_daily`, `symbols` selects
products such as `MA`; in `cn_products`, it selects canonical keys such as
`CN:CZCE:MA`. Use `venue="CZCE"` for venue-specific CN bars. A bare US `MA`
continues to mean Mastercard. Vendor sources are retained and are not silently
combined or ranked. `us_prices` retains raw OHLC and adjusted `adj_*` columns;
select the correct basis for research. Adjustment does not make a price series
point-in-time knowledge. Index membership start/end dates and provenance remain
available in `index_membership`; consumers must apply their research cutoff.

Private datasets require `MarketData(private=True)` or CLI `--private`.

## Assess the requested window

Use [the readiness contract](READINESS.md) before interpreting table-wide counts
as a usable window. `client.readiness(contract)` requires a source filter and an
explicit caller-owned list of session dates. It reports exact missing dates;
method/model equality filters keep independently derived series separate.
It assesses daily presence only. Live-store assessments are provisional even when
all requested dates are present. Published presence does not establish full grids,
quote freshness, NBBO, release integrity or consumer acceptance.

## Deliver data to applications

```bash
./scripts/native.sh data-providers export us_prices ./runtime/releases/spy-example \
  --symbol SPY --start 2026-10-01 --end 2026-10-16 --format parquet

./scripts/native.sh data-providers serve --host 127.0.0.1 --port 8765
curl 'http://127.0.0.1:8765/v1/data?dataset=us_daily&symbol=SPY&limit=100'
```

An export directory contains `data.parquet` (or `data.csv`) and `manifest.json`
with contract version, filters, row count, schema, UTC creation time and SHA-256.
Parquet exports store 128-bit integers as decimal text to preserve every digit. The manifest records logical types in `schema` and file types in `physical_schema`; consumers can cast the text back to HUGEINT/UHUGEINT in DuckDB.

Both files are published by one directory rename. Release names are immutable;
choose a new directory for each release. Consumers can keep reading exports
while writers maintain the databases. Local DuckDB readers can be temporarily
blocked by a writer; the HTTP API reports this as 503, not as an empty dataset.

The HTTP API supports `GET /v1/datasets`, `GET /v1/status?dataset=NAME` and
`GET /v1/data`, with `dataset`, repeated `symbol`, repeated `contract_id`, `start`,
`end`, `source`, `venue`, and bounded `limit` data parameters. It
does not accept SQL. Set `DATA_PROVIDERS_TOKEN` for bearer authentication; a
non-loopback bind requires a token. This small service is intended for trusted
local/network deployments; use TLS termination when crossing an untrusted network.

`qc_work` has an optional local research adapter. Run this next example on the
host in the QC checkout and its own environment:

```bash
cd ../qc_work
uv pip install --python .venv/bin/python -r requirements-data.txt
.venv/bin/python scripts/export_market_data.py --symbol SPY \
  --start 2026-10-01 --end 2026-10-16 --output .local/market-data/spy-example
```

This produces a local research dataset. QuantConnect cloud custom-data formats,
hosting and ObjectStore uploads remain separate, explicit application work.


## Pinning releases

```python
client = MarketData(backend="parquet")
print(client.release.id)
same_release = MarketData(backend="parquet", release=client.release.id)
next_client = client.refresh()  # new client; existing readers stay pinned
```

`backend="parquet"` requires a published release. `backend="duckdb"` explicitly
reads live ingestion state for diagnostics and may encounter an active writer.
An explicit `release=` cannot be combined with `backend="duckdb"`.

The HTTP service runs on the host. The loopback example is reachable from that
host; Windows access from WSL depends on its networking configuration. No Docker
port mapping is needed. Keep remote binds authenticated as described above.

## Interpretation contract

- Underlying broker 30-day IV, option-derived near-45-DTE IV, and calculated RV
  are separate source/method series. QC needs 504 comparable completed option-derived
  observations; screener 252-session IV is a separate requirement.
- OHLC/trades/EOD are not NBBO. Index volume is not invented.
- Bar completion, capture time and historical availability time differ. Unknown
  publication clocks remain unknown; a successful join alone is not entry-safe proof.
- Definitions, mapped BRAIN fields and contract inventories are metadata, not
  evidence of historical value coverage or exact formula equivalence.
- Source quotes and broker-native Greeks are provider data. Portfolio risk,
  calibration, rankings and scenarios remain consumer-owned.

See [architecture](ARCHITECTURE.md), [historical options](HISTORICAL_OPTIONS.md),
[daily indicators](DAILY_INDICATORS.md) and the requirements index (private path `docs/requirements/README.md`; [access boundaries](EVIDENCE.md#private-evidence)).

## Intraday vendor Greeks

`us_option_intraday_greeks` retains source-specific observations keyed by source,
contract, interval timestamp, sampling frequency and vendor model version. It is
separate from daily IV and daily research observations. Use `contract_ids`,
`symbols`, `start`, `end` and `source` filters with a pinned Parquet release.

```python
MarketData(backend="parquet", release=release).read(
    "us_option_intraday_greeks", symbols=["SPX"],
    start="2026-07-13", end="2026-07-13",
    source="thetadata_v3", limit=20,
)
```

Invalid IV/underlying inputs remain flagged, with their original source values.
Unknown originating quote and historical availability timestamps remain null.
`model_version` identifies the requested vendor implementation, not an established
pricing-model identity. Unsupported Greeks remain null; native units and model
acceptance are recorded separately. Do not join EOD Greeks to morning quotes or
interpret sampling frequency as quote freshness.

### Official index-option expiry settlement

`us_index_option_settlements` exposes retained official Cboe SPXW settlement
values, keyed by source, trading symbol, expiry and raw-response SHA256. Read
with `symbols=['SPX']` and an expiry date range. Each receipt remains a version;
consumers must select a version explicitly when multiple receipts exist.
`received_at` is acquisition time. Null `published_at` and `available_at` mean
historical publication/availability is unknown; do not use receipt time or an
ordinary index close as a substitute. These are expiry settlement index points,
not option premiums, executions or daily indicative option marks.

Offline import:

```bash
./scripts/native.sh python -m data_providers.jobs.history.import_cboe_settlements --delivery PATH
```

The importer verifies retained raw hashes and exact
source values, stages before merging, and makes no network requests. Publish
and verify the resulting release before treating it as Parquet delivery.

### Bounded at-time quote evidence

`us_option_quotes` with `source='thetadata_v3_at_time'` exposes the four bounded
SPXW timestamp probes separately from the existing `thetadata_v3` sampled grid.
`interval_ts` is the requested at-time cutoff. `quote_ts` is the last NBBO vendor
source timestamp returned for that cutoff, converted from America/New_York to
UTC. It is not separately proven exchange generation time or historical client
receipt time. `quote_age_ms` is cutoff minus source timestamp; recompute age for
any later decision. `available_at` remains null, so known quote age alone does
not pass strict historical availability or establish executable prices.

The October 19–23 nine-symbol daily refresh is available in
`us_underlying_research_daily` with `source='thetadata_v3'`. Its
`session_scope='provider_eod_includes_extended_hours'` distinguishes these raw
vendor EOD records from regular-session closes. Preserve null historical
availability; neither vendor `created` nor current retrieval time supplies it.

### Current-issuer grouping versus historical option roots

Legacy `us_option_bars.underlying` may group an issuer's history under its current
symbol using `runtime/plans/option_root_history.csv`. The original OCC contract
identity stays in `symbol`; do not treat the grouping field as the ticker that
traded on every historical date. The October 26 retained-stage audit found 4,092
EQR rows grouped under VMRK, all before the documented August 18, 2026 symbol
conversion; every other field matched. See
`docs/requirements/OPTION_ROOT_LINEAGE_AUDIT_20261026.json`. This mapping does not
authorize relabeling broker fills, generalizing adjusted deliverables, or
claiming point-in-time symbol availability.

## Reference navigation

[Dataset reference](DATASETS.md) lists every logical name and its actual time/symbol
filter fields. [CLI and HTTP reference](CLI_REFERENCE.md) documents option placement,
HTTP errors and interface differences. [Configuration](CONFIGURATION.md) explains
runtime precedence. The consumer Options Workspace may already use port 8765;
choose a different free port for the standalone API when both are needed.
