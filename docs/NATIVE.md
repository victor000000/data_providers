# Native Linux / WSL environment

Docker is not required. Development, tests, builds and maintenance run
on the host from `/home/vic/data_providers`.

## Setup

Install `uv` and `make`, then run:

```bash
make setup
make test
make build
./scripts/native.sh data-providers datasets
./scripts/native.sh data-providers maintain --scope trades
```

Setup uses the committed `uv.lock`, a project-private Python 3.14, and `.venv-native/`. The current portable
interpreter is `runtime/python/bin/python3.14`; fresh installations download
Python into `.python-native/`. It never syncs into the old container virtualenv. During migration, compatible
Linux distributions can be copied into the fresh native environment and reconciled
against the lockfile; native entry points and package paths are rebuilt. On this installation `.venv` is a compatibility symlink to
`.venv-native`; the obsolete `.venv-container` was deleted at the user's request.
Do not run `uv sync` without `UV_PROJECT_ENVIRONMENT=.venv-native`; use `make setup`.

Use `scripts/native.sh COMMAND ...` for jobs. It selects the native environment,
UTC, the existing `runtime/`, and the Windows trading-export directory. Environment
overrides remain supported. It does not initialize, copy, or relocate databases.
The host user must own writable runtime directories. Trading inputs remain source
files: loaders read them and preserve original bytes in receipts.

## Brokers and optional native tools

`IB_HOST` and `FUTU_HOST` default to `127.0.0.1` for native jobs. Set these explicitly
if brokers run on another host or WSL networking does not expose Windows loopback.
`IB_PORT` and `FUTU_PORT` retain their adapter settings. Readiness checks must not
request history or submit orders. API readiness is distinct from historical access.

The optional local Theta terminal requires Java 21 or newer; a configured remote
terminal does not need local Java. The installed private JRE is under
`runtime/tools/java21/`, with `current` selecting the verified installation.
`scripts/ensure_thetadata.sh` checks that location before system Java paths. Native solver builds may need a compiler, CMake,
and their documented libraries. Core ingestion, tests and publication do not need
Docker or a local Java process.

## Theta local transport and passive network evidence

`ThetaDataClient` explicitly bypasses environment proxies for literal loopback
terminals (`127.0.0.1`, `localhost`, and `::1`), even when `NO_PROXY` is absent.
Remote terminal endpoints retain their existing environment behavior. The HTTP
connection pool is capped at four connections per client and performs no automatic
retries. This is not a global request allocator: the shared history lock, immutable
receipts, entitlement checks, and existing conservative pacing remain in force.
Pacing uses a monotonic clock so wall-clock corrections cannot add spurious waits.

The local Mihomo project can run a passive WSL Theta observer. Its current status
is mirrored to `runtime/var/maintenance/theta-network-observer/latest.json`; the
host deployment keeps rotating logs under its `state/native-theta/` directory.
It reads Java/TCP counters and new receipt summaries, without issuing requests,
restarting the terminal, or changing routes. Compare TLS bytes, RTT, CPU and
receipt completion times during normal acquisition. Idle samples and locally
decoded CSV byte rates do not establish upstream bandwidth. Any active comparison
still requires audited, disjoint windows and the existing request reservations.

## Theta response processing optimization (2026-10-27)

Fresh history responses are decoded and parsed once, with raw-byte checksum
verification before returning rows. Cache reads hash and parse the same byte
buffer, avoiding a second disk read and a file-change window. UTF-8 and universal
newline behavior match retained CSV receipts. Shared history locking, pacing,
timeouts, durable in-flight markers and failed-key replay protection are retained.

An offline replay of a retained 9,728,380-byte response (61,462 rows), alternating
three runs per version, reduced median local request processing from 0.458 to
0.238 seconds (1.92x). Parsed row hashes matched. This measures local processing,
not Theta server or network throughput; no market-data requests were sent.
Evidence is saved in `runtime/var/maintenance/theta-client-speedup-20261027/benchmark.json`.
New worker processes load the change; existing workers keep their imported code.

Parallel history batches also reuse one HTTP session per worker thread (at most
four with the explicit four-slot setting), retaining connection pools across requests. Sessions are never shared
between threads and close after workers finish, including exceptional exits.
The default global two-slot limit, pacing, frozen request keys and no-replay receipts
remain unchanged. This reduces connection setup overhead; it does not establish
an upstream bandwidth improvement. Healthy running workers are not restarted
just to load this optimization.

For audited small metadata or closing-sample scopes, new workers can explicitly
set `THETA_HISTORY_SLOTS=4` after checking that the current terminal advertises
four concurrent requests. The ceiling is four; other values fail closed. Paid
v3 four-slot mode removes the local global and per-client 0.5-second start
delays: Theta limits Standard to four account-wide concurrent requests, with no
separate paid requests-per-second limit. Default two-slot mode retains its
conservative pacing. See the [current v3 concurrency contract](https://www.thetadata.net/docs/Articles/Data-And-Requests/Concurrent-Requests.html). This
uses the same admission, transport, per-key, slot and pacing locks and remains
compatible with existing two-slot and legacy exclusive workers. Existing workers
are preserved. Mixed workers can still wait behind an older caller limited to
slots zero and one. This opt-in is not a recommendation for large full-session
transfers: the earlier four-request whole-session measurement showed no gain.
Compare completed receipt rates on fresh disjoint scopes, preserve slow in-flight
requests, and keep server latency separate from network bandwidth. Trial evidence
lives in `runtime/var/maintenance/theta-small-request-four-slot-20261029/`.

## Worker migration and recovery

An explicitly queued local import can call `maintenance.execute(...,
wait_for_writer=True)` to wait on the existing kernel writer lock. The default
still fails immediately when another writer is active. This avoids repeated
polling races for a queued journal import without bypassing writer ownership.
Do not replace a running importer or publisher to change its waiting policy.

Inspect [Operations](OPERATIONS.md) before launching or stopping any worker. Use
shared kernel locks across host and container; never remove lock files. A process
ID is namespace-specific: container PIDs cannot be used as host PIDs.

For migration, stop at a boundary with no in-flight receipt, preserve the frozen
queue and stage, verify raw hashes and stage row counts, then resume only untouched
keys with the native interpreter. All successful, empty, denied, failed and uncertain
ledger keys remain immutable. Do not migrate by restarting a broad acquisition.

Historical receipts may contain `/root/data_providers/` paths. Resolve that prefix
to this checkout when reading archived evidence; do not rewrite original receipts
or hashes. Current jobs write native paths. The former container environment and Docker recovery backups were
deleted on 2026-10-23; the migration retention instruction is historical.

The former Docker setup is retired. See [Docker retirement](DOCKER.md) for
cleanup scope and backup deletion. Docker Desktop is not part of native
startup, broker access, tests or builds.

### Theta terminal recovery timer (2026-10-27)

The user service `thetadata-recovery.timer` calls the existing
`scripts/ensure_thetadata.sh` at boot and every 120 seconds after the previous
check completes. Unit sources live in `ops/systemd/`. It uses the configured
provider endpoint/credentials, never restarts an existing terminal, and does not
launch maintenance. `flock --close` serializes recovery without leaking the lock
to the detached JVM. Inspect `journalctl --user -u thetadata-recovery.service`.
Disable with `systemctl --user disable --now thetadata-recovery.timer` if required.
A healthy terminal does not certify quote freshness or subscription entitlements.

### Opt-in bounded parallel history (2026-10-27)

For an audited allocation, construct `HistoryClient(..., parallel=True)` and use
`request_many([(endpoint, params), ...])`. It streams results in allocation order,
uses two worker threads by default (four with `THETA_HISTORY_SLOTS=4`), keeps at
most one additional task queued per worker, and reuses one HTTP session per
worker thread. The same cross-process slots cap all participating workers;
default two-slot start pacing retains at least 0.5 seconds between starts;
explicit paid four-slot mode uses concurrency admission without a start delay. Default clients
remain serial. The shared gate excludes all legacy exclusive-lock downloaders,
so deploying this code does not interrupt or overlap an existing legacy transfer.

A durable global exact-key claim protects new opt-in requests across receipt
folders. Missing/corrupt claim targets fail closed. Existing failed, empty, denied
and uncertain receipts are never retried. Claims do not index older folders or
establish semantic non-overlap: audit all historical receipts and entitlement
boundaries before allocating new windows. Do not enable concurrency by restarting
healthy workers. Keep staging writes in the consuming thread or separate stages.
Measured local parsing improvement does not establish parallel network speedup;
compare receipt HTTP timings and passive upstream telemetry during authorized work.

New workers can use `request_many(audited_requests, ordered=False)` to consume
responses as they complete. This avoids blocking completed work behind an earlier
slow response and keeps at most one outstanding request/result per worker, without
an additional prefetch queue. Callers must derive identities from each receipt's
parameters rather than zipping results with the original request list. Stage writes
still belong to the consuming thread. Closing the iterator cancels unstarted work;
started requests finish and retain their receipts. Default result ordering is
unchanged. These changes remove local scheduling limits; they do not establish a
network speedup, and existing healthy workers are not restarted to adopt them.

For the [one-minute SPXW request](requirements/QC_0DTE_CUSTOM_AXIS_1M_20261029.md),
the Standard-compatible bulk unit is one dated expiry with `strike=*`,
`right=both`, and `interval=1m`, within the frozen pilot and overlap audit. Preserve
the full listed chain and actual session bounds. Do not expand to every expiry
or ticks merely to benchmark speed. Theta's separate market-wide
[`option/flat_file/quote`](https://www.thetadata.net/docs/operations/option_flat_file_quote.html)
requires Pro and exposes only the latest seven calendar days (documentation checked
2026-10-29); it does not solve this Standard-plan historical backfill. Additional
network adapters do not add account-wide concurrent request slots. Route tests must
coordinate the terminal's shared upstream connection and preserve in-flight work.

Native one-minute bulk measurements and the coordinated route trials are recorded
in `runtime/var/maintenance/theta-1m-throughput-20261029/report.json`. Distinguish
decoded CSV throughput, compressed upstream counters and time waiting for shared
slots. Changing dates or contract counts is not a controlled same-payload test.
The dedicated-adapter trial did not verify terminal reconnection within its bound;
the exact original routing was restored before acquiring the untouched requests.
Generic TLS retention across a proxy reload does not establish Theta reconnection.
Do not repeat idle upstream socket closures without resolving that limitation.
The read-only `/v3/terminal/mdds/status` system endpoint reports MDDS state without
requesting market history; a local open port alone does not establish connectivity.

### Bounded exact publication verification

Large temporal partitions now use bidirectional daily `EXCEPT ALL` checks rather
than one year-sized hash comparison. Dates from both sides and a separate NULL
bucket preserve extra-row and duplicate detection. Immutable partition formats,
fingerprints, raw receipts and atomic CURRENT promotion remain unchanged. This
addresses the observed 1 GB exact-comparison OOM without redownloading inputs.

Daily exact comparisons use direct half-open temporal bounds so DuckDB can prune
row groups without casting every source timestamp. The NULL bucket and both
directions of EXCEPT ALL remain mandatory. An offline 500,000-row, 50-day
synthetic benchmark measured about 13.7x faster comparisons; this is not a live
publication or network throughput claim. Existing workers retain their loaded
code and are not restarted for this change.

### Bounded admission backlog (2026-10-27)

New scheduler instances retain admission until their transport slot is acquired.
This limits newly scheduled parallel work to two active slots and at most one
unslotted shared-gate holder. The gate → request-key → slot acquisition order is
unchanged so new and already-running workers can coexist. Existing processes
retain their imported scheduler; do not restart healthy downloads to adopt this
change. Durable request claims, no-replay checks and pacing are unchanged. This
bounds local queue contention; it is not a measured network-throughput gain.

For configuration precedence and shared variables, see [Configuration](CONFIGURATION.md).
For exact command/HTTP arguments, see [CLI and HTTP reference](CLI_REFERENCE.md).
