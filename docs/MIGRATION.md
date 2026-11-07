# Extraction from dte_21_screener

The extraction used the working tree on 2026-10-17, including uncommitted fixes.
It did not reset, stash or commit either application's existing work.
`migration/extraction.json` records each original path, new path, and the SHA-256
of its original content. Local, ignored tar archives under `.local/` preserve
the original source before replacement. They contain code, not live credentials
or database snapshots.

| Former source | New implementation |
| --- | --- |
| `data/` | `data_providers.data` |
| `datasources/` | `data_providers.providers` |
| `shared/` | `data_providers.shared` |
| `apps/data_ops/` | `data_providers.jobs.data_ops` |
| Market ingestion from `apps/screener/` | `data_providers.jobs.history` |
| Trade imports/capture from `apps/trade_log/` | `data_providers.jobs.trades` |
| `compute/bs.py` | `data_providers.compute.bs` |
| `apps/audit/check_duplicates.py` | `data_providers.jobs.audit.check_duplicates` |
| `market_data/scripts/` | `data_providers.warehouse` |
| `scripts/update_all.sh` | `data_providers.maintenance` supervisor |

159 Python files were extracted; 154 former implementation files now forward
to the package. Package initializer files remain at the legacy locations.
Normal imports alias the actual module object, preserving private state,
monkeypatching and reload behavior. Direct script execution forwards to the
canonical module's `__main__`. Some obsolete internal shims are themselves
retained as aliases to another shared job.

Screening/ranking decisions, plan building, portfolio risk and reports stay in
the screener. The Black-Scholes implementation moved because both ingestion
and consumers use it; QuantLib shared-library discovery uses this project's
`third_party/qliv/qliv.so`, with the established Python fallback on fresh systems.
Native sources, cached builds, and tools are owned here.

The screener declares an editable dependency on `../data_providers`. Its bootstrap
resolves `DATA_ROOT` through provider configuration and keeps application `ROOT` separate. Its fast check
installs the shared package into the temporary uv environments it creates.
The old `scripts/update_all.sh` forwards flags and exit status to shared
maintenance. `--cn-only`, `--us-only`, `--with-bars`, `--with-settle`, and
`--workers` remain accepted. Supervised maintenance is now serial, includes
the underlying-price refresh before US IV calculations, and records trade-log
steps alongside market updates.

The screener's warehouse tests now import the canonical modules, and its scanner's
minimum module-count guard reflects the smaller application. The coverage
deficit ratchet remains intact. Standalone tests include extracted regressions,
new delivery/maintenance checks, and an import-boundary test. They run against
an isolated temporary runtime seeded with test mappings, never live databases.

The local editable dependency expects sibling checkouts. A screener-only clean
checkout/CI job must also obtain this project, or replace the editable source
with an internally published wheel. No public registry release or repository
remote is assumed by this migration.

## Current software-release boundary

The extraction/relocation counts below are historical. The development release
0.2.0.dev0 adds daily-presence SDK/CLI assessment without rekeying history, changing
stored schema or moving runtime paths. Consumers must still use their independent
environments and an explicit tested provider commit/wheel. An editable sibling
path alone does not pin source code for CI. The public sanitized source excludes
private runtime and requirement receipts; it can lag the live installation.
See [Evidence access](EVIDENCE.md) and [Development](DEVELOPMENT.md#ci-and-release-review).

## Runtime relocation (2026-10-17)

The user subsequently authorized complete storage and maintenance independence.
All live stores, raw files, staging, credentials, backup/journal files, operational
state/logs, Yahoo warehouse data, terminal tools and maintained universes were
renamed into `data_providers/runtime`. Native sources/builds moved to this
project's `third_party`. The 46 rename operations preserved all 34,267 file
hashes and inodes (12,322,306,894 bytes); every database table retained its row
count. The detailed private inventory is `.local/runtime-relocation.json`.

Application reports, design documents, source and trading-plan outputs remain in
the screener. No old database/raw-data links were left in that checkout. Its direct
readers, test fixtures and subprocess entrypoints now use the shared data root.
The coverage baseline was rebased to the smaller application: 39/69 modules are
covered, reducing the allowed uncovered deficit from 136 to 30. Tests for moved
shard operations are also run in the provider package.

The original `data_providers` installation was a separate container built from Python and pinned uv with
locked dependencies, a private project environment/interpreter, Java, and native runtime
libraries. It mounts no screener checkout or user-wide Python installation.
`TRADING_LOGS_SOURCE` configures the host export-folder bind mount; inside the
container `TRADING_LOGS_DIR=/root/trading_logs` is independent of data-home depth.
Maintenance and monitoring commands live in this project's `scripts/`; legacy
shell commands forward here. No downloads are started during migration checks.

The already-tested data-project environment was made self-contained by copying
its Python distribution into `runtime/python` and repointing `.venv` there. The
consumer's interpreter was not moved. This avoids cold dependency downloads for
the local cutover while removing the user-wide toolchain dependency entirely.
That Docker setup was retired on 2026-10-23. The current installation uses
`.venv-native` and the existing private interpreter directly on Linux/WSL;
see [Native environment](NATIVE.md) and [Docker retirement](DOCKER.md).
