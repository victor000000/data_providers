# Validation and evidence

Validation has several independent meanings. Report the one actually checked:

| Check | Establishes | Does not establish |
| --- | --- | --- |
| Isolated provider tests | Tested parser/schema/transport/SDK behavior | Live feed availability or complete coverage |
| Wheel/source build | Package can be built | All deployment inputs/native binaries are present |
| Consumer fast gate | Its recorded checks passed, if all pass | Full strategy baseline performance |
| Raw receipt/hash | Observed response bytes and provenance | Entitlement to unqueried history or valid NBBO |
| Staging/merge checks | Staged keys/values represented in canonical stores | Source completeness |
| Release verification | Published schema/content/checksums agree with captured stores | Realtime semantics, historical availability or research suitability |
| Consumer baseline validation | Consumer-owned accounting/model/performance evidence | Provider coverage beyond the actual input artifacts |

## How to verify a change

See [Development](DEVELOPMENT.md#checks) for isolated tests/build and compatibility
checks. For a data delivery, inspect its dated run result and publication receipt,
then verify the pinned release. Never run production maintenance merely to test code.

Read [Operations](OPERATIONS.md) for current delivery/checkpoint locations. Test counts,
release IDs and source dates belong to dated evidence; a previous passing result is
not a guarantee that the current consumer checkout passes.

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

Known gaps remain explicit in [requirements](requirements/README.md), including
QC 504 option-derived observations, executable historical NBBO, point-in-time action
and contract metadata, CN source delay/book clocks and damaged raw-cache provenance.
Successful publication does not remove those gaps.
