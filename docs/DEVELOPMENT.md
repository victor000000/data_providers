# Development and documentation

The package owns provider transport, ingestion, schemas, maintenance and delivery.
It must never import consumer application code. [AGENTS.md](../AGENTS.md) defines
working rules; [Native environment](NATIVE.md) describes this installation's private environment.

## Source map

| Path | Responsibility |
| --- | --- |
| `data_providers/providers/` | Source adapters, transport and provider behavior |
| `data_providers/jobs/`, `warehouse/` | Ingestion and maintenance jobs |
| `data_providers/data/schema/`, `data/staging/` | Constraints, source staging and verified merge |
| `data_providers/runtime.py` | Runtime/input path resolution |
| `data_providers/catalog.py` | Logical datasets, keys and filter columns |
| `data_providers/lake.py` | Immutable publication, verification and release views |
| `data_providers/client.py`, `server.py`, `cli.py` | Consumer SDK, bounded HTTP and CLI |
| `tests/` | Isolated regression coverage |
| `scripts/` | Shell operations/helpers; inspect before invoking legacy pipelines |
| `third_party/` | Native solver sources, builds and binaries |
| `runtime/tools/` | Private terminal tools/configuration |
| `.local/` | Installation-specific recovery scripts/logs; not packaged interfaces |

Legacy screener shims alias canonical module objects. Preserve private names,
monkeypatching and reload behavior. See [Migration](MIGRATION.md) for original paths.

## Checks

Run on the host from `/home/vic/data_providers`:

```bash
make test
make build
```

`make test` uses `scripts/native.sh python -m pytest -q` and an isolated temporary runtime.
It must not require live credentials, orders, downloads or a production warehouse.
`make build` creates the wheel and source distribution. Use targeted tests during
implementation; complete the checks appropriate to the change.

After compatibility changes also run the screener's `bash scripts/check_all.sh
--fast` in its own native environment. That application retains its own environment and verification rules. Record failures and their scope; do not turn a passing import/shim subset
into a passing full consumer gate. Documentation-only revisions need link, CLI
example and configuration checks, not a live download cycle.

Create or reconcile the native environment with `make setup` on the Linux/WSL
host. It selects `.venv-native` and the locked private Python. The old
`.venv-container` and Docker recovery backups were deleted on 2026-10-23; do not recreate them. CI uses the
same native Make targets. No Docker daemon or image is required.

## Documentation rules

- Keep [README](../README.md) short and task-oriented. Use [the docs index](README.md)
  to find one authoritative guide for each topic.
- Setup/configuration belongs in Native environment; active recovery/checkpoint locations in
  Operations; source ingestion behavior in Maintenance; consumer reads in Data access.
- Dated requirements and delivery receipts remain in place. Add links and supersession
  notes rather than rewriting hashed historical evidence or moving consumer handoffs.
- General guides link to live status paths instead of copying counters or PIDs.
- Separate implemented behavior, requested coverage, source/access blockers and
  validated delivery. Explain exact versus equivalent formulas explicitly.
- Date validation evidence and distinguish unit regressions, publication integrity,
  actual source coverage and consumer baseline performance.
- Check commands against the local CLI and configuration. Keep secrets/account
  contents out of examples. Destructive/lifecycle commands require an active-work audit.

## Change recipes

### Add or change a provider

Start from the owning [provider module](PROVIDERS.md), active scope and exact
consumer requirement. Resolve vendor spellings with the strict symbol resolver
before I/O. Keep raw responses and native request parameters/checksums, including
empty/refused/failed results. Normalize into source staging, preserving source,
method, units, UTC/source-local/unknown clocks and stable historical identities.
Validate stage keys and values before an exact transactional merge. Existing
request ledgers, shared provider admission and writer locks still apply.

A test should exercise meaningful parser, identity, clock, error or merge
behavior using fixtures. Do not call a paid/live source to test a change. When
source bytes are sensitive, keep real receipts in runtime and use sanitized
fixtures rather than copying credentials/accounts into tests.

### Add a consumer dataset

1. Define typed schema and keys in `data/schema/` and its initialization path.
2. Implement source staging/validation/verified merge in the owning job.
3. Add the deliberate public/private contract to `catalog.py`, choosing the actual
   time and symbol filter fields. Catalog registration does not create the table.
4. Verify reads, unsupported filters, private guards and pinned publication with
   isolated fixtures where the change affects them.
5. Update [Datasets](DATASETS.md), [Data access](DATA_ACCESS.md) and any changed
   [CLI/HTTP contract](CLI_REFERENCE.md). Record source/method limitations.

Publication enumerates physical tables/views beyond catalogued SDK datasets.
Review privacy and schema compatibility before adding new stored information.
A breaking change needs an explicit migration and consumer checks; do not use
`--allow-schema-change` to conceal accidental removals or type changes.

### Change shared runtime or compatibility behavior

Read [Configuration](CONFIGURATION.md) and [Migration](MIGRATION.md). Keep read
paths free of store creation, preserve legacy module-object aliases/private
names/reload behavior, and avoid imports from consumer application code. Run
focused runtime/alias tests, the project checks appropriate to the change and the
screener fast gate in its own environment for compatibility changes.

### Keep references current

| Source change | Documentation to review |
| --- | --- |
| `runtime.py`, native/setup scripts | Native environment, Configuration |
| `cli.py`, `server.py`, `client.py` | CLI/HTTP reference, Data access |
| `catalog.py`, schema/filter contracts | Dataset reference, Data access |
| `maintenance.py`, trading scan/loaders | Maintenance, CLI reference, Operations |
| `lake.py`, partitioning/publication | Architecture, Dataset reference, Validation |
| Provider limits, clocks, receipts | Provider reference, Data provenance, relevant method guide |
| New requirement or delivered selection | Requirements index and dated evidence, without rewriting old receipts |

Date source reviews. Keep live process state in runtime evidence. Documentation
checks should validate relative links, CLI parser examples and catalog mappings
without executing acquisition or publishing a live release.
