# v0.2 design and implementation plan

## Outcome

Consumers can explain whether an explicitly requested data window is present in a
pinned release, reproduce the assessment, and distinguish publication from their
own acceptance. Provider data qualification does not establish strategy suitability.

## Existing foundation

Keep the native environment, runtime ownership, DuckDB ingestion, immutable Parquet
publication, exact merge verification, receipt protection and module aliases.
MarketData.status currently exposes row counts and date bounds. Those bounds do
not establish contiguous coverage. Existing research coverage and dated receipts
remain evidence; they are not interchangeable universal completeness certificates.

## Delivery stages

1. Implement a read-only readiness contract and SDK/CLI assessment. This is the
   first independently testable increment and the scope of the initial code change.
2. Add durable workflow state to recurring package jobs, migrating each existing
   dated coordinator at an audited idle boundary. Preserve original receipts and
   active owners. Do not replace a running worker as part of a software upgrade.
3. Extend the assessment envelope into delivery handoffs with stage inclusion,
   whole-release verification references and separately recorded consumer acceptance.
4. Add effective-dated native reference qualification for contract terms, corporate
   actions and actual futures links. Unknown effectivity remains unknown.

Stages 2–4 need source-specific designs before implementation. A generic retrying
scheduler or inferred historical terms would violate existing recovery contracts.

## Readiness contract v1

Input JSON contains exactly: version=1, dataset, symbols, sessions, filters and
required_columns. The filters object is required and must contain source or
datasource_tag; only required_columns is optional. Symbols and sessions
are nonempty unique lists. Sessions are explicit ISO dates from a caller-owned
exchange calendar. The assessor never guesses weekdays, holidays or listing dates.
Filters are equality predicates on existing columns with non-null scalar values.
At least one explicit source discriminator (source or datasource_tag) is mandatory;
method/model identifiers can be additional filters. Contract hashes include every
input, using canonical JSON. Symbol strings retain catalog semantics, including
venue-qualified CN identifiers. No vendor translation or I/O is performed.

A session is present when at least one qualifying row exists for that symbol on
that UTC date. Required columns must be non-null; numeric values must also be
finite. Duplicate rows do not create additional covered sessions. This is daily
presence, not an assertion of full intraday grids, NBBO, valid IV methodology,
positive prices, or field-specific thresholds. Such semantics need explicit later
contract extensions rather than implicit defaults.

Output records version, normalized contract, SHA-256 contract identity, pinned
release/backend, assessment UTC time, per-symbol present/missing dates and qualifying
row counts. Complete presence in a published release is coverage_complete; live
DuckDB assessments are provisional and never ready. Consumer acceptance and release
integrity verification are explicitly not established by this operation. Unknown
columns, unsupported types, missing stores and corrupt releases fail visibly.

## Implementation

Use MarketData._connection so release pinning, private access and missing-store
behavior match existing readers. Validate all dynamic identifiers against DESCRIBE;
parameterize values. Aggregate selected dates in SQL; do not load all raw rows into
Python. Enforce bounded input sizes. Expose MarketData.readiness(contract) and
`data-providers readiness CONTRACT.json`. Print JSON to stdout; callers choose
whether and where to retain it. No files, downloads, claims or locks are mutated.

## Self-review before implementation

- Date bounds alone can hide interior holes: compare exact requested session sets.
- Combining vendors can fabricate completeness: require source equality explicitly.
- A calendar synthesized here could be wrong: require explicit caller sessions.
- A complete live table is not reproducible delivery: mark provisional, ready=false.
- Publication is not consumer acceptance: expose separate unknown acceptance state.
- SQL identifiers and contract shape are untrusted: validate before executing queries.
- Null/NaN required values cannot close a session: exclude them from qualified rows.
- Intraday presence is weaker than grid completeness: name/document this limitation.
- Adding a method does not require changing legacy aliases or published schemas.

## Validation

Use isolated DuckDB fixtures to test internal holes, vendor isolation, duplicates,
invalid required values, malformed contracts, private access, missing stores,
release pinning and CLI JSON. Run the project test suite and build. Do not acquire
market data or publish the live runtime to test this feature.

## Implemented increment and final review — November 7, 2026

Version before: 0.1.0. Development version: 0.2.0.dev0; target release: 0.2.0.
Implemented stage 1 in readiness.py, MarketData.readiness and the readiness CLI.
Package metadata, runtime __version__ and lockfile project version agree.
Usage and qualification limits are documented in READINESS.md.

Final review checked dynamic SQL identifiers/parameters, UTC timestamp boundaries,
private access, absent-store behavior, source isolation, finite numeric fields,
contract normalization, duplicates, release pinning and CLI JSON. A captured-output
fixture failure was fixed by draining test publication output before invoking CLI.
No production publication or source requests were used for validation.

Validation: full make test passed 699 tests and 28 subtests, with two existing
dependency deprecation warnings. Subsequently added UTC boundary/SQL value test:
all 14 readiness tests passed. Focused Ruff check passed with --no-cache (the
existing cache directory was not writable). make build produced the wheel and
source distribution for 0.2.0.dev0. Logs: /tmp/data-providers-v02-tests-final.log
and /tmp/data-providers-v02-build.log; these temporary logs are not release proofs.

Remaining work: stages 2–4 are designed at the architectural level and have not
been implemented. This increment must not be reported as completion of the entire
v0.2 roadmap. No migration of running owners was attempted. The repository's lack
of a committed baseline still prevents a normal commit-relative change review.


## Follow-up self-review and remaining prerequisites

The contract documentation incorrectly described filters as optional. Corrected:
a source discriminator is mandatory; only required_columns may be omitted.
The ready field is retained for the initial v1 response contract and means exactly
coverage_complete AND a pinned published release. It is not a release-verification
or consumer-acceptance gate. READINESS.md includes an SDK example that shows all
three evidence states together. No runtime behavior changed in this follow-up.

Concrete remaining prerequisites (not requests for routine design approval):

- Stage 2: inventory each recurring coordinator's frozen queue, state transitions,
  locks, receipt formats and owner; implement and test its package replacement
  offline; establish an actual idle boundary before any live ownership migration.
  No current idle boundary was verified in this design task. This constrains live
  migration, not further offline implementation.
- Stage 3: define a versioned binding between requirement hash, selected-stage
  hashes, exact inclusion proof, whole-release verification and explicit consumer
  acceptance. Existing readiness results do not contain these proofs. An acceptance
  event must come from the consumer; the provider cannot infer it from CURRENT.
- Stage 4: inventory native per-field evidence and define effective-date intervals,
  conflicts and unknowns for exact contracts. Current terms cannot backfill historical
  effectivity. Missing native historical evidence remains an upstream limitation;
  schema and offline validation work can proceed independently.

Stage 1 has no known failing checks. Its limits remain daily presence, caller-owned
calendar/session identity and explicit method filters where methods coexist.
It does not yet validate provenance receipts for every qualifying row or economic
quality thresholds. Source-control baseline absence limits commit-relative review,
not the test results. This is an incomplete v0.2 roadmap, with a validated first increment.

Changed source and metadata: data_providers/readiness.py, data_providers/client.py,
data_providers/cli.py, data_providers/__init__.py, tests/test_readiness.py,
pyproject.toml and uv.lock. Documentation: docs/README.md, docs/READINESS.md and
this design. Retained validation evidence is indexed by
validation/v02-readiness-20261107/receipt.json. These are software-check receipts,
not live data-delivery or whole-release proofs.


## Source-control handoff

The owning repository is /home/vic/data_providers, branch main. Before committing
this task it had no commits and an empty index. The task commit therefore records
complete contents of the explicitly touched source/metadata/documentation files;
it cannot express their changes against a historical baseline. Other pre-existing
untracked files, runtime stores, credentials, build artifacts and caches are excluded.
This focused initial snapshot is not a standalone baseline for the entire project.
No live worker files were migrated and nothing is pushed.


## Coherent publication baseline and CI follow-up

The initial workspace task commit 44fe688 is a partial root snapshot, not the
release commit. The coherent publication checkout is
/home/vic/data_providers/.local/github-publication-20261105/public-source,
based on 7d1c417908347ef4d4f274546481f5f67de210c7 (main), matching the November 5
CI inventory. Its initial worktree and index were clean. Release work uses branch
codex/v02-readiness. The only client.py change on this baseline is the new
readiness method; unrelated later workspace provenance changes are excluded.
No deployed workers, credentials or runtime stores are transferred.

The full suite on this older publication baseline initially passed 681 tests and
28 subtests. Additional regressions cover method isolation, early input-size
rejection without store creation and omitted quality columns/empty populations.
CI keeps the existing clean native setup and full make test/build commands,
adds readiness lint, emits JUnit results and retains results/distributions for
14 days. It requires no provider secrets or live services. Final results and
publication source hashes are retained in the adjacent validation receipt.
The publication suite has fewer tests than the live workspace because unrelated
post-baseline changes were deliberately excluded; these populations are not merged.


## Clean-checkout verification and publication blocker

Implementation commit: 17486f4a9ec1f96919cbd6ca5dc5e7c1aec69483.
A separate clone at /home/vic/.local/share/project-publications/data_providers-v02-20261107
checked out that exact commit, ran make setup into its own private native environment,
and passed 684 tests plus 28 subtests (two dependency warnings), focused lint,
and make build. JUnit XML and command logs are retained locally beside receipt.json;
generated build/runtime artifacts and logs are excluded from the publication commit.
This follow-up changes only documentation and verification records.

Push was attempted with prompting disabled. It failed because the configured
credential helper refers to an inaccessible former container executable, leaving
no username/credential available. The connected GitHub create-tree operation
also returned HTTP 403 Resource not accessible by integration. No permissions,
credentials or live services were changed. Remote main was independently checked
at 7d1c417908347ef4d4f274546481f5f67de210c7; codex/v02-readiness was absent.
Consequently no hosted CI run exists for this implementation. Prior baseline CI
success must not be reported as validation of the new commit. A write-authenticated
push and terminal exact-SHA hosted CI result remain outstanding.
