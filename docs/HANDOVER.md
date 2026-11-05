# Reusing and handing over this project

This is the durable checklist for a future operator, contributor or consumer.
It does not declare live workers healthy or delivered coverage complete. Consult
[Operations](OPERATIONS.md) for installation-specific checkpoints and recovery.

## Read in this order

1. [AGENTS.md](../AGENTS.md): ownership, market scope and preservation rules.
2. [Native environment](NATIVE.md) and [Configuration](CONFIGURATION.md): interpreter,
   effective runtime, mounted inputs and network endpoints.
3. [Architecture](ARCHITECTURE.md): raw/staging/canonical/published boundaries.
4. [Data access](DATA_ACCESS.md) and [Dataset reference](DATASETS.md): consumer contracts.
5. [Maintenance](MAINTENANCE.md) and [Operations](OPERATIONS.md): plan, locks and recovery.
6. [Requirements](requirements/README.md): exact consumer requests and dated evidence.

US and mainland CN underlying PV and listed options are the active acquisition
scope. Actual futures, calendars, actions and specifications remain included.
HK/HKG/Hong Kong and other listing markets remain excluded, even when old
inventories contain them. Preserve historical evidence without scheduling those
markets. Portfolio risk, strategy screens, calibration, rankings and scenarios
belong to consumer applications; native broker/account fields remain source data.

## Establish what exists before taking action

From the checkout, these commands inspect paths, processes, catalog and CLI help:

```bash
pwd
ps -eo pid,etime,args
./scripts/native.sh data-providers datasets
./scripts/native.sh data-providers maintain --help
./scripts/native.sh python - <<'PYCODE'
from data_providers.runtime import data_home, trading_logs_dir
print(data_home())
print(trading_logs_dir())
PYCODE
```

Read saved status files selectively; do not dump credentials, accounts or huge
embedded manifests into a handoff. A status labelled `running` is only a saved
claim. Compare the actual process, kernel lock owner, last completed receipt,
staging progress and finalizer. A persistent lock file is not itself an active
lock. Do not unlink it to make a new worker start.

Do not run setup against a healthy running installation merely to inspect it.
When environment installation/reconciliation is needed, use `make setup` under
the native procedure. Do not alter a live worker's environment or restart it
solely to pick up an optimization.

## Define completion at the correct boundary

| Boundary | Evidence required | Remaining question |
| --- | --- | --- |
| Requested | Scoped requirement, instrument list, dates, fields and method | Is acquisition authorized and source-supported? |
| Attempted | Immutable request key and outcome | Did the response contain usable observations? |
| Retained | Original bytes and verified raw checksum | Has normalization preserved identity, units and clocks? |
| Staged | Typed rows and stage validation | Are those exact keys/values in canonical storage? |
| Merged | Transaction result and exact selected-stage inclusion | Is this content in a consumer-visible release? |
| Published | Release ID and immutable manifest | Have release integrity and selected-row inclusion been checked? |
| Delivered | Verified release and exact inclusion proof for the requested selection | Does the consumer accept coverage, timing and method? |

A checksum proves retained bytes, not full market coverage. A release verifier
checks the retained release; it does not compare today's evolving live stores or
prove a particular pack reached that release. Publication's source comparison
and the delivery's selected-stage inclusion proof address those different claims.

## Choose the next action from evidence

| Situation | Next action |
| --- | --- |
| Healthy acquisition/finalizer owns locks | Preserve it; inspect new completions and publication frontier |
| Source/API is reachable but history stalls | Inspect actual error text, transport and receipt timings; connectivity alone does not justify replay |
| Failed/empty/denied/uncertain Theta receipt exists | Reuse its recorded outcome; do not delete it or bypass the ledger with `retry` |
| Raw retained, stage incomplete | Audit hashes and normalization frontier; finish offline work where supported |
| Stage merged, publication pending | Locate the existing finalizer/coordinator and frozen watermark; avoid duplicate snapshots |
| Published release fails verification | Retain failure evidence and previous good release; diagnose files without refetching market data |
| Consumer missing rows | Check release ID, filters, private access, source and exact inclusion before calling it an acquisition gap |
| Mounted trading source unavailable | Restore the authoritative mount; archived inputs do not establish current freshness |
| Worker absent after host restart | Audit locks, saved state and in-flight keys; use that worker's documented bounded recovery |

A recovery allocation must retain its scope, queue hash, stage and request ledger.
Theta exact-key protection does not prove semantic non-overlap across different
parameter spellings or windows. Audit earlier receipts before any new allocation.
Admission freezes stop new reservations; already admitted calls can drain.
Failed and uncertain attempts stay recorded even if other calls finish successfully.

## Durable handoff record

Use the following structure for an operational handoff. Fill evidence paths and
facts, leaving unknowns explicit. It is a template, not a new receipt format that
replaces the existing worker's files.

```text
Recorded at: <UTC time; user-facing Asia/Shanghai time when reporting>
Request/requirement: <path and version/date>
Authorized selection: <markets, instruments, dates, fields, source/method>
Environment: <native interpreter and effective runtime; no secrets>
Worker: <script/module, process checked at time, kernel lock evidence>
Frozen work: <queue/checkpoint paths and hashes>
Request outcomes: <nonempty / empty / denied / failed / uncertain>
Raw evidence: <receipt paths and SHA-256 checks>
Stage: <path, validated rows and unfinished frontier>
Canonical merge: <transaction/result and exact inclusion proof>
Publication: <release ID, verification and selected-row inclusion paths>
Consumer access: <logical dataset, filters and private/public setting>
Blockers: <source/access/time/identity/method gaps, including unknowns>
Next action: <bounded remaining action, owner and prerequisites>
```

Keep private records inside the protected runtime. General documentation should
link to evidence locations and semantics instead of copying account rows, tokens,
PIDs or volatile progress counters.

## Future development workflow

1. Locate the owning adapter/job/schema and requirement before editing.
2. Resolve symbols before provider I/O; preserve historical/native IDs.
3. Retain original bytes and clocks, then normalize to a separate stage.
4. Validate keys, types, units, raw/adjusted basis and exact merge inclusion.
5. Add/update the public catalog only when the consumer contract is deliberate.
6. Update the authoritative task guide and matching reference for changed behavior.
7. Run focused isolated tests and required build/compatibility checks; never use
   full live maintenance as a code test.

See [Development](DEVELOPMENT.md) for checks and module ownership,
[Provider reference](PROVIDERS.md) for source entrypoints, and
[Validation](VALIDATION.md) for the limits of each proof.
