# Shared data project

Documentation starts at [docs/README.md](docs/README.md). Use
[Native environment](docs/NATIVE.md) for the environment and [Operations](docs/OPERATIONS.md)
for live checkpoints/recovery before starting maintenance.

This project owns provider adapters, ingestion, schemas, data maintenance and
delivery. Consumer strategies, screens, plan building, portfolio risk and trade
execution belong to their applications.

Risk profiles, premium-profit rankings, dollar-delta/gamma analysis and portfolio
scenario reports belong to `/home/vic/dte_21_screener`, including ad hoc scripts.
Keep broker-native Greeks and account risk fields as source data here; do not
turn data maintenance into portfolio-risk reporting.

## Active market-data scope

Acquire only US and mainland CN underlying price/volume (PV) and listed options:
stocks, ETFs, indices where applicable, and actual futures contracts. HK/HKG/Hong
Kong and all other listing markets are out of scope, not deferred delivery gaps.
Older BRAIN metadata, manifests and requirements do not authorize those markets.
Preserve historical raw evidence; do not schedule excluded acquisition/backfills.
Supporting calendars, corporate actions and contract specifications remain included.
Do not invent index volume or substitute trade bars for NBBO. Independent broker
execution and trading-journal maintenance remains authorized.

## Environment

Run this project directly on the Linux/WSL host with its private native environment:

```bash
make setup
make test
./scripts/native.sh data-providers datasets
```

The locked environment is `.venv-native`, backed by the portable private Python in
`runtime/python` (fresh installations use `.python-native/`). `.venv` is a compatibility symlink after migration. The old
`.venv-container` and Docker recovery backups were deleted at the user's request
on 2026-10-23. Keep host dependencies in the native environment. Docker is not required; use the native setup for development, tests and maintenance.
Use `scripts/native.sh` for maintenance and watcher commands. Keep the existing
runtime and source receipts, and inspect locks/checkpoints before resuming jobs.
`qc_work` and the screener retain their independent environments.

## Runtime ownership

Code and storage roots are different. Use `data_providers.runtime` for ingestion
paths and `MarketData(home=...)` for read access. The live storage is `runtime/`
inside this project, selected by the local config and native command environment.
The user authorized its relocation here on 2026-10-17. Do not recreate stores in
consumer projects or relocate it again without a request. Reads must not create
missing stores. The screener keeps source/report paths separate from DATA_ROOT.

Maintaining data covers US market data, CN market data, and trading logs.
The source trading-log folder is the mounted Windows folder, normally
`/mnt/c/Users/victo/Documents/trading_logs`, with `TRADING_LOGS_DIR` as an explicit override. Preserve
the newest-modified export per file type, including original bytes and provenance.
Source receipts and normalized fills are distinct; do not count duplicate
exports from different broker programs as additional executions.

Whenever the user asks to maintain trading logs, rescan the mounted source folder
on that invocation; do not rely on an earlier inventory or status report. The
canonical routine is `data-providers maintain --scope trades --execute --publish`
through `scripts/native.sh` on the host. Its first step hashes current input files, detects
added/changed/removed files, and records the newest-modified export per type in
`runtime/var/maintenance/trading-inputs/latest.json`. An unavailable source blocks
trading loaders and broker refreshes; archived copies do not establish freshness.
Identify extensionless CN position/account/order exports by their headers as well
as filenames. Preserve their raw receipts and normalize broker snapshots through
`import_cn_tracker`; never infer executions from changed positions or journal rows.

Preserve raw versus adjusted price conventions, UTC timestamps, venue-qualified
CN identities, source provenance and the staging/verified-merge lifecycle.
Use IBKR spelling as the canonical US instrument name. Resolve provider spellings
through `data_providers.data.symbols` before constructing requests; use
`translate(native_name, source, target)` when the input is another vendor's name.
Keep stock tickers and native option roots separate (`massive` versus
`thetadata_option`): `BRK B`, `BRK.B`, and `BRKB` identify different source
representations of the same underlying. Unknown mappings must fail before I/O.
Preserve native symbols in raw receipts and stable historical contract IDs; never
silently rekey published history. Keep CN identities venue-qualified.
Retain unfinished downloads on failure. US fill capture covers both underlying
and option minutes; CN underlying capture uses the rolling Sina feed, while true
CN option-minute coverage remains an upstream limitation.

## Verification

`make test` uses an isolated temporary runtime, never live credentials or stores.
`make build` creates a wheel and source distribution. After compatibility changes,
also run the screener's `bash scripts/check_all.sh --fast` in its own native environment.
Do not run a full maintenance/download cycle merely to test code changes.

Native solver sources and binaries live in this project's `third_party/`;
the terminal's tools and private configuration live in `runtime/tools/`.
Legacy modules in the screener alias canonical package module objects; keep
private names, monkeypatching and reload behavior intact. The package must never
import consumer application code. See `docs/MIGRATION.md` for the source map.
