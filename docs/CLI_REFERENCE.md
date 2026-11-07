# CLI and HTTP reference

Reviewed against [cli.py](../data_providers/cli.py) and
[server.py](../data_providers/server.py) on 2026-11-07. Commands run from
`/home/vic/data_providers`. Examples containing `RELEASE_ID`, `DATASET` or a new
export destination require real values. Planning and help do not launch market
acquisition; execution, initialization, exports and publication are explicit writes.

## Command structure

```text
./scripts/native.sh data-providers [GLOBAL_OPTIONS] COMMAND [COMMAND_OPTIONS]
```

Global options belong before the command:

| Option | Contract |
| --- | --- |
| `--home PATH` | Explicit runtime for this client; see configuration precedence |
| `--private` | Enable private dataset access; standalone publication includes private stores only with this flag |
| `--backend auto` | Default: pin current Parquet release if present, otherwise existing read-only DuckDB |
| `--backend parquet` | Require a published release; fail if missing |
| `--backend duckdb` | Read live ingestion state; may conflict with a writer |
| `--release RELEASE_ID` | Pin an immutable release; incompatible with DuckDB backend |

JSON is the normal result representation. Maintenance execution also prints step
progress, and `serve` prints its listening address. Do not assume their stdout is
one JSON document. Errors go through the CLI parser and return nonzero.

```bash
./scripts/native.sh data-providers --help
./scripts/native.sh data-providers maintain --help
./scripts/native.sh data-providers --private datasets
```

## Inspect and read

| Command | Result |
| --- | --- |
| `datasets` | Logical names visible with the current private-access setting; catalog membership is not evidence of populated coverage |
| `schema DATASET` | Ordered field names and logical types in the selected backend/release |
| `status DATASET` | Row count, configured time column/bounds, backend and release |
| `read DATASET` | Ordered row objects, default limit 10,000 and maximum 100,000 |
| `readiness CONTRACT.json` | Exact source-filtered daily presence for explicit symbols/session dates; see [contract](READINESS.md) |
| `lake status` | Selected release manifest, or `status: unpublished` |
| `lake verify` | Verify the selected/current immutable release files without provider I/O |

```bash
./scripts/native.sh data-providers schema us_option_quotes
./scripts/native.sh data-providers status us_daily
./scripts/native.sh data-providers --backend parquet read us_daily \
  --symbol SPY --start 2026-10-01 --end 2026-10-16 --limit 5
./scripts/native.sh data-providers --release RELEASE_ID lake verify
```

`read` and `export` accept repeated `--symbol`, plus `--start`, `--end`,
`--source` and `--venue`. A filter fails when the table lacks its required field.
Source filtering uses the physical `source` column; a table with only
`datasource_tag` does not automatically support `--source`. Dates are inclusive;
a date-only timestamp end includes the whole UTC day. Use the
[dataset reference](DATASETS.md) to identify the actual symbol/time fields.
`read --limit` must be a positive integer. There is no CLI pagination interface.

Python additionally supports `columns`, `contract_ids` and `read_tagged`.
HTTP supports repeated `contract_id`; CLI does not expose that filter.
`ORDER BY ALL` makes results deterministic, not a promise that the first limited
rows are the latest observations. A table-wide min/max does not prove continuity
or completeness for one symbol, source or contract.

## Readiness contract

```bash
./scripts/native.sh data-providers --release RELEASE_ID readiness contract.json
```

`contract.json` must include version 1, dataset, unique symbols, explicit session
dates and filters containing source or datasource_tag. required_columns is optional.
See [Readiness](READINESS.md) for the full JSON and SDK example. This command
prints an assessment and writes no runtime files. Incomplete coverage is a normal
assessment, so it still returns exit code 0; malformed input/read errors return
nonzero. Automation must inspect the JSON rather than infer readiness from exit 0.
There is no HTTP readiness route. `ready` means daily presence in a pinned release,
with release integrity and consumer acceptance separately not_assessed.

## Export and initialize

```bash
./scripts/native.sh data-providers --release RELEASE_ID export us_daily \
  ./runtime/releases/spy-unique-example \
  --symbol SPY --start 2026-10-01 --end 2026-10-16 --format parquet
```

`export DATASET DESTINATION` writes filtered `data.parquet` or `data.csv` plus
`manifest.json` by a directory rename. The destination must not exist. It records
filters, logical and physical types, checksum and originating release. Exports
are not constrained by the read row limit. Preserve 128-bit integer text encoding
when loading Parquet. Private exports retain private directory permissions.

`init` writes schemas and seeds to the selected runtime. It is for deliberate
initialization, not a repair for a missing dataset on a consumer read. Inspect
[Native environment](NATIVE.md) and [Configuration](CONFIGURATION.md) first;
do not initialize a second live warehouse.

## Maintenance planning and execution

Without `--execute`, `maintain` prints the selected step list only.

| Option | Meaning |
| --- | --- |
| `--scope all` | Default: US, CN and trading records |
| `--scope us` / `--us-only` | US maintenance plus standard trading steps |
| `--scope cn` / `--cn-only` | CN maintenance plus standard trading steps |
| `--scope derivatives` | US futures/catalogs and CN derivative jobs plus trading steps |
| `--scope trades` | Input scan, receipts, journals, broker refresh, fill minutes and duplicate audit |
| `--with-bars` | Include optional US stock/ETF option-bar refresh jobs |
| `--with-settle` | Expand selected CN settlement/history jobs |
| `--futures-full` | Pass full-history mode to the US futures job where included |
| `--workers N` | Positive worker count passed to selected CN jobs; not a global provider concurrency budget |
| `--execute` | Run the plan with writer ownership, step logs and recorded outcomes |
| `--publish` | After successful execution, publish using the supervisor's private publication path |

Every scope begins with an authoritative trading-folder scan and contains the
standard trading steps. A US/CN scope is not a market-only refresh. Plans can
contain large backfills even without optional flags. Preview the exact plan and
check existing workers before executing it.

```bash
# Preview only:
./scripts/native.sh data-providers maintain --scope trades
# Canonical user-requested trading maintenance, after the operational audit:
./scripts/native.sh data-providers maintain --scope trades --execute --publish
```

Execution uses a six-hour default timeout per step. A failure blocks later steps
in the same group. A failed input scan additionally blocks receipt, broker and
trade groups; unrelated groups can still run. Overall success requires no failed
groups. This is not an all-or-nothing database transaction. Inspect `result.json`
and logs after any partial failure. Failed maintenance returns nonzero and does
not automatically publish. `--publish` without `--execute` only previews a plan.

## Publication

```bash
./scripts/native.sh data-providers --private lake publish
./scripts/native.sh data-providers lake verify
```

These are operational commands to use after the existing writer/finalizer audit,
not verification steps for a documentation edit. Standalone publication defaults
to public stores. `--allow-schema-change` is a `lake` option for an explicit
breaking migration; it is not a routine repair flag. Review removed fields/types,
consumer compatibility and retained-release access before using it.

The CLI has no writer-wait option. Explicit queued waiting and coalesced-release
reuse are Python publication/supervisor interfaces described in
[Operations](OPERATIONS.md). Preserve existing lock ownership.

## HTTP contract

```bash
# Select a free port; 8765 may already belong to the consumer workspace.
./scripts/native.sh data-providers --backend parquet serve --host 127.0.0.1 --port 8766
curl 'http://127.0.0.1:8766/v1/datasets'
curl 'http://127.0.0.1:8766/v1/status?dataset=us_daily'
curl 'http://127.0.0.1:8766/v1/data?dataset=us_daily&symbol=SPY&limit=5'
```

| Route | Response |
| --- | --- |
| `GET /v1/datasets` | Contract version, visible datasets and release |
| `GET /v1/status?dataset=NAME` | Dataset count/time bounds, backend and release |
| `GET /v1/data?dataset=NAME` | Rows, release, limit and `possibly_truncated` |

Data parameters: `dataset`, repeated `symbol`, repeated `contract_id`, `start`,
`end`, `source`, `venue`, `limit`. There is no SQL endpoint, write endpoint,
column-projection parameter or pagination cursor. `possibly_truncated=true`
means the result reached the requested limit; it is not proof of more rows.
The service refreshes its client per request: automatic clients can see new
CURRENT releases between requests, while explicitly pinned clients stay pinned.
Record the response release when joining results from separate requests.

| HTTP status | Interpretation |
| --- | --- |
| 200 | Request completed; rows may legitimately be empty |
| 400 | Invalid dataset, parameter or unsupported filter |
| 401 | Missing/wrong bearer authorization when a token is configured |
| 403 | Private access disabled |
| 404 | Unknown endpoint |
| 503 | Missing store/release table, active-writer conflict or other dataset failure |

Set `DATA_PROVIDERS_TOKEN` through the private environment. When configured,
every route requires `Authorization: Bearer <token>`. Non-loopback listeners
require a token. Start with `--private` only when private datasets are intended
for those authenticated clients. Filesystem permissions remain the underlying
storage security boundary. For larger selections use a pinned filtered export.
