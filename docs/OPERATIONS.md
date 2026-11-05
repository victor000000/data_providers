# Operations and recovery

Run commands on the host from `/home/vic/data_providers` through `scripts/native.sh`
with the private native interpreter. [Native setup](NATIVE.md) covers paths and networking. This guide
locates live evidence; it deliberately does not freeze changing counters or PIDs.

## Inspect before starting work

1. Read [AGENTS.md](../AGENTS.md) and the [active market scope](requirements/MARKET_DATA_SCOPE_20261019.json).
2. Inspect current status, checkpoint, raw receipts, processes and locks. Preserve
   a healthy downloader and its finalizer; do not start an overlapping broad run.
3. Compare request, staged-row and verified-delivery counters with the previous
   check. An empty/refused completed request is not a downloaded observation.
4. Check source/access evidence before retrying failures. Reuse successful caches;
   do not repeatedly request unchanged entitlement refusals or buy subscriptions.
5. Record UTC evidence and report user-facing progress in Asia/Shanghai.

| Evidence under `runtime/` | Purpose |
| --- | --- |
| `var/maintenance/consumer-monitor/status.json` | Current reconciliation; use its active acquisition object, not older top-level counters |
| `var/maintenance/consumer-monitor/check-*.json` | Dated progress comparisons and consumer requirement checks |
| `var/maintenance/iv-ten-year-20261019/status.json` | Frozen-universe frontier, request/staged counters and run state |
| `var/maintenance/iv-ten-year-20261019/delivery-*.json` | New canonical rows and exact verified release receipts |
| `var/maintenance/broker-recovery/latest.json` | Bounded TWS/Futu readiness, watcher decision and last resume |
| `var/maintenance/broker-recovery/events.jsonl` | Readiness/recovery history |
| `var/maintenance/trading-inputs/latest.json` | Last authoritative trading-folder scan; not proof of freshness today |
| `var/maintenance/<run-id>/result.json`, `summary.json` | Supervisor step outcomes and publication outcome |
| `lake/CURRENT`, `lake/snapshots/<release>/manifest.json` | Published release pointer and immutable content manifest |

Useful inspection (not acquisition):

```bash
cat runtime/var/maintenance/broker-recovery/latest.json
cat runtime/var/maintenance/iv-ten-year-20261019/status.json
ps -eo pid,etime,args
tail -n 20 .local/resume-ten-year-iv.log
./scripts/native.sh data-providers lake verify
```

The IV status embeds a large publication manifest; select needed fields with
Python rather than dumping it into every progress report. `.local` recovery
scripts are installation-specific tools, not packaged CLI commands.

## Frozen ten-year underlying IV run

The active run uses `.local/resume-ten-year-iv.py`, its same-named log, frozen
`universe.json`, retained request/raw caches and `staging.duckdb` under the IV
maintenance folder. It acquires `worker.lock`, resumes the saved symbol frontier,
validates identity, preserves cumulative counters and invokes the existing merge/
publication finalizer. Earlier failed windows behind the frontier remain separate
coverage gaps. Four damaged-cache keys are recorded in `damaged-cache-20261021.json`;
they are not silently refetched or declared repaired by a successful publication.

Never rerun the original broad script over newly calculated date windows.
Never splice this broker underlying30-day series into QC option-derived ATM IV.
The original 4,602-symbol attempt pass is complete. Selected failed-window retries
use `.local/retry-failed-iv-windows-20261022.py` and the separate
`iv-ten-year-20261019/retry-failed-20261022/` status, immutable queue and ledger.
Every existing ledger key is skipped, including failed and uncertain attempts.
Inspect the [historical-timeout diagnosis](requirements/IV_HISTORICAL_TIMEOUT_DEBUG_20261022.json)
before recovery. API readiness alone does not prove historical transfer health.
Scheduled maintenance must diagnose stalled transfers and apply bounded fixes;
preserve a healthy worker and never cycle unchanged failures blindly.
The [October 22 gap diagnosis](requirements/IV_GAP_DIAGNOSIS_AND_DELIVERY_20261022.json)
separates proven pre-source periods from unresolved service responses. Error 162
is a general HMDS message; use its text to distinguish permission refusals,
timeouts, cancellations and no-data responses. Reclassification never clears an
existing request ledger. New October 19–21 intervals are tracked separately under
`runtime/var/maintenance/iv-gap-debug-20261022/recent*/status.json`; the recovery
controller and monitor recognize these workers without restarting the old pass.
See [acquisition requirement](requirements/TEN_YEAR_IV_ACQUISITION_20261019.md)
and [damaged-cache recovery](requirements/IV_DAMAGED_CACHE_RECOVERY_20261021.json).

## Automatic broker recovery

`.local/watch-broker-recovery.py` checks TWS/Gateway and Futu readiness every 60s.
Its exclusive `broker-recovery/watcher.lock` prevents duplicate watchers. A
300-second launch cooldown, IV worker lock, writer lock, frozen frontier and stage
count guard a resume. Successful/empty/refusal caches are retained; failed receipts
are archived before a checkpoint resume.

It only resumes the recognized three-transport-failure `stopped_error` state.
It leaves healthy running/publishing jobs untouched. TWS probes use read-only
server time with `StartupFetch(0)`; Futu uses global READY/login state. Neither
probe makes historical or quote requests. Futu readiness alone does not trigger
market acquisition. API availability and upstream historical transfer are distinct:
IBKR1100/1102 and actual completed receipts must be assessed together.

If no watcher owns its lock, launch it in the native environment:

```bash
./scripts/native.sh python -u .local/watch-broker-recovery.py >> .local/watch-broker-recovery.log 2>&1 &
```

The general maintenance heartbeat checks the watcher and restarts it if absent.
A host restart does not restart detached watcher/download jobs automatically.
If a restart leaves stale `running`/`publishing` status, audit processes, kernel
locks, stage counts, raw receipts and the last finalizer before reconciling state.
Do not reset status just to satisfy the resume condition. Lock files are persistent;
kernel lock ownership, not file existence, determines whether work is active.

## Other completed and bounded work

- The original Massive US run `catalog_us_20261017T102145Z` has completed its
  553-contract priority queue. Inspect `priority-result.json`, `priority-handoff.json`,
  `priority-queue.json`, `prices.json` and `.local/futures-priority.log`; older broad
  logs and pending queue entries can be stale. Do not restart it. Any authorized
  failed/unfinished selection uses that run's frozen windows and `--prices-only
  --priority-roots`, not a duplicate catalog discovery.
- EWZ's October 19 capture/consumer acceptance is completed. It is a bounded historical
  snapshot, not a continuously live feed. See the [exact-leg handoff](requirements/EWZ_PROPOSED_EXACT_LEG_LOOKUP_20261019.md).
- CN's October 20 afternoon exact-contract snapshot is completed. Do not automatically
  repeat it. Strict realtime acceptance remains blocked by delay/book-clock evidence
  and skew failures. [Progress](requirements/CN_REALTIME_PNL_PROGRESS_20261020.json)
  and `cn-held-source-20261020/latest.json` locate the durable packet.
- `.local/check-cn-native-publication-20261020.py` checks exact canonical content
  against a verified publication without requesting another market capture.
- Completed Alpaca 292-bar and 74-calendar deliveries retain their separate evidence;
  neither metadata nor a calendar resolves QC method/availability blockers.

## Publication and status reporting

The existing finalizer owns normal IV publication. Preserve its staging and locks.
After a stopped run, `.local/publish-retained-iv.py` can reuse the same merge/publish
function offline, holding the worker lock and preserving the download frontier.
Do not run it alongside an active worker. A newly built release is not reported as
verified until its receipt says verification passed. See [Architecture](ARCHITECTURE.md).

Report completed attempts, nonempty rows, staged/unpublished rows and verified
published rows separately. Include measured interval, worker and upstream health,
latest verified release, new deliveries, explicit blockers and new requirements.
State when no data was delivered or progress stopped. ETA is conditional on recent
pace and connectivity; an attempt-pass ETA is not a promise of complete valid
coverage. Consumer regression checks are not validated baseline performance.

### Continuous option publication

Full-period acquisition must not gate incremental delivery. The October 29 pack
pipeline keeps downloads, ten-session normalization, serial exact merges and
publication independent. Its incremental coordinator is
`.local/publish-qc-fullperiod-1m-incremental-20261029.py`; inspect
`qc-fullperiod-1m-delivery-20261029/publication-status.json` and the actual PID.
The coordinator retains the verified pack frontier, caches checks against an
unchanged immutable release, and uses daily bidirectional exact comparisons.
It reuses existing verified snapshots and coalesces an additional snapshot when
at least 10M merged rows remain unverified after 15 minutes. Completion flushes
the remaining backlog. This is a publication policy, never a download retry.

Before requesting that snapshot, the coordinator freezes the already-staged pack
list in `next-merge-publication-watermark.json` and lets the serial merger reach
that boundary. New downloads and later normalization packs do not move the target.
This prevents repeated snapshots from taking the writer lock while a large ready
merge backlog remains. The watermark is archived with its successful release;
it is independent of full-period acquisition completion.

The October 29 user-directed unblock recovery records a read-only publisher
checkpoint under `publication-merge-priority-20261029/`: every prepared file is
retained and hashed, source merges remain committed, and CURRENT is unchanged.
The affected batch022 finalizer now waits for exact inclusion in a coalesced
release instead of starting its own snapshot. A bounded publication gate holds
only `lake/publish.lock`, allowing the canonical writer to drain the 107 completed
normalization packs. Inspect `gate-status.json` and the actual merger before any
recovery. It releases on the target boundary, merger failure or deadline; never
remove its lock file. The coordinator's blocking publication is gated by the same
ready-pack target, so it cannot take the writer while that target is incomplete.

Daily partition reuse can follow immutable release ancestry when an older process
has replaced the daily layout with yearly files. Reused files still need matching
source fingerprints, schema and current file checksums. This avoids repeating a
year-to-day conversion while preserving the pinned release model.

A queued coalescing publisher can pass `reuse_if_current_changed=<prior release>`
to `lake.publish`. After acquiring the usual locks, it returns an existing
advanced release instead of creating another snapshot. Private publication does
not reuse a public-only release. The caller must still verify that release and
prove exact selected-stage inclusion; a changed CURRENT alone is not delivery.
The full-period coordinator uses this to avoid a duplicate build behind another
active publisher. Default publication behavior remains unchanged.

For the seven remaining current-tail IBKR keys, a serial expiry handoff may wait
under `last7-watch-rearm.lock` while the existing six-hour watcher remains healthy.
It makes no API calls. Only after that watcher exits with its bounded-expiry state
does it check the old PID, exclusive watcher lock, unchanged 4,447-key frontier,
frozen queue hash, seven absent receipt keys and 22,031-row retained stage. It
archives the expired watch receipt before starting one replacement watcher. Any
dispatched recovery, changed frontier or different lock owner cancels rearming.
Inspect `last7-watch-rearm-status.json` before starting another handoff.

`lake.publish(..., wait_for_writer=True)` queues on the same kernel writer and
publication locks instead of repeatedly missing a short idle interval. The
default remains fail-fast. Only one publisher owns those locks; a queued process
does not justify restarting an active publisher. Quote and intraday Greek tables
now use immutable UTC-day partitions, with source fingerprints grouped in one
pass. Each changed partition still passes schema, fingerprint and exact multiset
validation before atomic release visibility. Unchanged days retain their original
files; the manifest remains compatible with existing pinned readers.

Lake readers use a private temporary spill directory for each DuckDB connection.
Concurrent in-memory readers must not share the working directory's default
`.tmp`: one reader's cleanup can remove another reader's active blocks. A scratch
I/O failure does not justify reacquiring source data. Preserve the failed status,
retain the verified pack frontier, and resume offline comparisons with isolated
scratch storage. Never delete a shared temporary directory while readers are active.

The one-time queued backlog publication records progress under
`qc-fullperiod-1m-publication-repair-20261029/`. Its successful whole-release proof
does not replace the individual pack inclusion proofs. Do not repeat it after
completion. An old coordinator may be replaced only at an audited idle boundary,
with no database/Parquet handles or writer/publication lock, preserving its
verified frontier and all receipts. Never interrupt a snapshot in progress.

Consumer chats are inspected read-only. Do not send messages without direct human
authorization. Provider reconciliation retains source paths and hashes. Risk,
calibration, premium/delta/gamma rankings and strategy scenarios remain consumer-owned.

## Dated Schwab export snapshots

`broker_snapshot_receipts` and `broker_snapshot_positions` are private SDK datasets
for retained PositionStatement CSVs. Their receipt hash and account identify an
immutable snapshot; repeated imports do not advance its source observation time.
`source_local_clock` is the header's wall-clock label, **not UTC**. Without an
explicit source timezone, `source_timezone` and `source_event_utc` remain null.
`ingested_at` records receipt processing separately. These imports currently cover
parsed nonzero option rows: `complete_account` is unknown and
`omitted_means_flat` is false. Neither missing contracts nor an empty parsed list
prove flat positions.

The CSV loader no longer appends these exports to legacy `broker_positions`
using import time as `as_of`. Existing legacy rows remain historical evidence;
that field does not certify the export's valuation/observation time. Join the new
receipt and position datasets by `receipt_sha256` and `account` for dated export
reads. Do not infer missing executions from snapshot differences.

Fill-minute maintenance merges the `option_minutes` and `underlying_minutes`
tables from retained `staging/alpaca.duckdb` before publication. The bounded
`jobs.data_ops.merge_fill_minutes` step holds the writer lock (or inherits the
maintenance supervisor's lock), refuses same-key value conflicts, verifies exact
inclusion, and retains the stage as acquisition evidence. It never merges an
active IV stage or starts a download. A successful downloader alone is not proof
that minute bars reached the published release.

## Local Options Workspace controls (v0.0.3)

The consumer webpage at `http://localhost:8765` calls `data_providers.web_ops` for
allowlisted plan previews, detached native execution and status. It does not own a
second data store. Default update scope is US + CN + trading logs; all market
updates retain the maintenance supervisor's mounted-export scan and trade-log steps.
No broad update is started simply by viewing the page.

Receipts and private worker output live under `runtime/var/maintenance/web-control/`.
`job.lock` serializes web updates and `health.lock` serializes bounded source checks;
the worker inherits its lock across the web-server lifetime. Existing acquisition,
writer and publication locks block overlapping updates. Failed/interrupted work is
shown without automatic retry; inspect canonical supervisor receipts before resuming.
`maintenance.execute(progress=...)` optionally publishes step boundaries to the web
receipt; ordinary CLI callers are unchanged. Successful updates call the existing
verified publication path; failed steps never trigger publication.

Source checks distinguish transport, endpoint access and quote freshness. Public
HTTP reachability does not certify live data. OPRA denials retain their original
check time for a 15-minute cooldown. The app polls saved status every five seconds
and optionally requests checks every 60 seconds while visible. Actual source event
times remain with contract quotes. Focused tests are `tests/test_web_ops.py`,
`test_web_health.py`, `test_web_runner.py` and `test_maintenance.py`; they use isolated
temporary runtimes and do not execute a real download cycle.

## Theta request receipt safety

`HistoryClient.request` reuses every existing receipt key, including failed,
invalid and in-flight attempts. The compatibility `retry` argument cannot bypass
this rule. It writes and fsyncs an in-flight receipt before HTTP and rechecks the
receipt after acquiring the shared Theta history lock. Interrupted requests stay
uncertain; do not delete receipts to retry. Successful raw caches still require
hash verification. This guard applies within the supplied receipt folder; callers
must also audit other folders and overlapping date ranges before scheduling new
requests. It does not establish a global semantic request registry.

## Theta queued admission freeze (2026-10-31)

Bounded recovery workers must stop new admission on the first unsuccessful
response or transport exception, while allowing previously admitted calls to
drain. `HistoryClient.request(..., admit=callback)` invokes the optional callback
after acquiring an account slot and checking retained receipts/global claims,
but before reserving a new key. The worker callback checks its stop event under
the same mutex used to set that event. Raising `HistoryAdmissionStopped` leaves
the queued key unclaimed, writes no receipt and sends no HTTP request. Cached
receipts remain readable after a freeze. The callback is opt-in and existing
callers retain their API behavior.

Admission precedes pacing and HTTP; a call admitted before the freeze is already
in flight for this policy and may subsequently finish. This does not cancel
sockets or prove network durability. Preserve failed/uncertain receipts and keep
cumulative failure counts when successful in-flight calls drain. A stable idle
CONNECTED status alone is insufficient evidence after recurring upstream stalls.

## November 5 Theta continuation

The original September 25 pass stopped at 08:39 Shanghai after four retained
HTTP 503 responses (`Network closed for unknown reason`). Its 1,608 accepted
observations were verified in release `20261105T003929Z_8924bc5b`. The separate
`theta-native-iv-sep25r1-20261105` batch contains only the 2,280 untouched keys;
its one-use IXC cold request succeeded and four-slot acquisition resumed at
08:48. Preserve both batch folders and all four failed keys. Recovery accounting
and global-audit exception reconciliation are retained in the original batch.

Current queue owners are `data-provider-theta-sep25r1-to-sep24-20261105`,
`data-provider-theta-after-sep24-recovery-20261105`,
`data-provider-theta-expiration-metadata-recovery-20261105`, and
`data-provider-theta-after-expirations-recovery-20261105`. Inspect their actual
PIDs and status before intervention. Metadata recovery records its controller
state in `theta-expiration-metadata-20261105/handoff-recovery-status.json`;
the original stopped `handoff-status.json` remains historical evidence. These
controllers retain immediate-predecessor verification and fresh admission gates.
The September 25 combined audit requires disjoint original/continuation keys,
complete original-plan accounting, unchanged failed receipts, and exact combined
publication before advancing to September 24. Failed outcomes remain coverage gaps.

The native daily chain resumed from the verified September 30 delivery. Its
prepared order and calendar evidence are recorded in
`runtime/var/maintenance/continuation-20261105/queue-evidence.json`. Inspect the
actual `data-provider-theta-*` user services and each batch's `status.json`,
`completion-status.json`, independent selection audit and publication proof.
Queued handoffs require exact immediate-predecessor delivery and fresh semantic
receipt checks; a queued service is not evidence of active source downloads.

The separate `data-provider-theta-expiration-metadata-20261105` service waits for
September 21's verified delivery before auditing and acquiring historical expiry
inventory. Its state is under `theta-expiration-metadata-20261105/`; preparation
and isolated tests are under `theta-expiration-metadata-audit-20261105/` and
`theta-expiration-metadata-tests-20261105/`. It retains one-use cold-start evidence,
uses the same four account-wide slots, stops on first failure, and verifies every
returned root/date and raw hash. Successful inventory is saved separately as
`verified-expiration-inventory.json`; it adds no canonical IV observations and
does not establish historical-session availability. Never restart this batch to
replay a failed or uncertain metadata key. Earlier daily acquisition still needs
its own frozen plan, overlap audit and source admission after metadata recovery.

`data-provider-theta-after-expirations-20261105` waits for that verified inventory,
then prepares September 18, 17, 16, 15 and 14 candidates and activates their serial
predecessor-gated handoffs. Its state and prepared-script hashes are under
`theta-after-expirations-continuation-20261105/`. The candidate builder rechecks
every metadata receipt/raw hash, complete plan accounting and open-session
calendar evidence. Exact-day listing evidence takes precedence, including an
exact inventory with no eligible expiry. These candidate lists remain separate
from frozen allocations; creating them sends no source requests.
