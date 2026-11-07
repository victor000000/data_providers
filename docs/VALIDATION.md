# Validation and evidence

Validation has several independent meanings. Report the one actually checked:

| Check | Establishes | Does not establish |
| --- | --- | --- |
| Isolated provider tests | Tested parser/schema/transport/SDK behavior | Live feed availability or complete coverage |
| Wheel/source build | Package can be built | All deployment inputs/native binaries are present |
| Consumer fast gate | Its recorded checks passed, if all pass | Full strategy baseline performance |
| Raw receipt/hash | Observed response bytes and provenance | Entitlement to unqueried history or valid NBBO |
| Staging/merge checks | Staged keys/values represented in canonical stores | Source completeness |
| Publication source comparison | Output exactly represents source tables while source transactions are pinned | Complete upstream history or consumer acceptance |
| Later release verification | Retained files agree with the immutable manifest, schema, counts, signatures and checksums | Equality with today's mutable ingestion stores or a particular staged pack |
| Readiness v1 | Daily presence for explicit sessions/source/required fields in the selected backend | Full intraday grids, economic validity, receipt verification or consumer acceptance |
| Consumer baseline validation | Consumer-owned accounting/model/performance evidence | Provider coverage beyond the actual input artifacts |

## How to verify a change

See [Development](DEVELOPMENT.md#checks) for isolated tests/build and compatibility
checks. For a data delivery, inspect its dated run result and publication receipt,
then verify the pinned release. Never run production maintenance merely to test code.

Read [Operations](OPERATIONS.md) for current delivery/checkpoint locations. Test counts,
release IDs and source dates belong to dated evidence; a previous passing result is
not a guarantee that the current consumer checkout passes.

## Documentation and CI checks

`make docs-check` validates canonical relative paths/Markdown anchors, all catalog
mapping rows and software-version agreement without opening stores. Dated evidence
and private runtime references are inventoried separately rather than executed.
The CI workflow runs locked native setup, this documentation check, readiness lint,
the full isolated suite and wheel/source builds. JUnit results and distributions
are retained as CI artifacts. See [Development](DEVELOPMENT.md#checks).
A locally passing workflow command is not a hosted GitHub CI result. Match any
hosted run to its actual commit; the [v0.2 design](V02_DESIGN.md) records earlier
publication blockers without asserting that permissions remain unchanged forever.

## Dated validation records

- [October 17–18 extraction, relocation and delivery validation](archive/VALIDATION_20261017_20261018.md)
  preserves the earlier report, including its historical test counts and then-current
  limitations. References to pending workers, old runtime paths or successful gates
  describe those dates only.
- October 21 weekly Schwab parsing: 486 provider tests and 28 subtests passed; wheel/source
  builds passed. The consumer fast gate still had risk-report and coverage/file-size
  failures; compile/import/shim/solver subsets passed. That is not a passing full gate
  or validated baseline. Evidence is referenced by the consolidated Schwab handoff
  under `runtime/var/maintenance/schwab-preopen-20261021/consolidated/`.
- [October 21 documentation review](DOC_REVIEW_20261021.md) records documentation
  checks; it does not claim a new production maintenance or performance run.

Known gaps remain explicit in requirements (private path `docs/requirements/README.md`; [access boundaries](EVIDENCE.md#private-evidence)), including
QC 504 option-derived observations, executable historical NBBO, point-in-time action
and contract metadata, CN source delay/book clocks and damaged raw-cache provenance.
Successful publication does not remove those gaps.
