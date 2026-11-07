# Documentation audit — November 7, 2026

## Scope and source authority

The native workspace and sanitized publication checkout were reviewed separately.
Native code includes later ingestion/provenance changes; those changes were not
copied into the publication branch as part of a documentation edit. Its baseline
for this phase is 8acb740 on codex/v02-readiness, descended from published main
7d1c417. The working software version is 0.2.0.dev0; future v0.2 stages remain
proposals. The earlier v0.2 and October review records are preserved.

All top-level canonical guides plus the project README were reviewed. Dated
requirements/archive/review records were inventoried and preserved; this is not a
line-by-line semantic revalidation of every historical receipt or every vendor
claim. Machine inventory and dispositions are in
[the audit manifest](validation/docs-audit-20261107/inventory.json).

## Coverage and disposition

| Guides | Review outcome |
| --- | --- |
| README, docs index | Added development-version/evidence boundaries and audit navigation |
| Native environment, Configuration | Checked runtime precedence, native bootstrap/extras, optional components, Python target vs minimum |
| Architecture | Added read-side readiness rationale and evidence-flow diagram; retained pinned publication design |
| CLI reference, Data access, Readiness | Added readiness command, source/session contract, exit semantics, SDK limits and absence of an HTTP route |
| Dataset reference | Reconciled every catalog row against each checkout; native has 54 entries and publication has 53 |
| Provenance, Providers | Reviewed source/method/clock distinctions against each checkout; public docs retain baseline-supported capabilities |
| Maintenance, Operations, Handover | Corrected historical monitor/worker authority, distinguished preview from fresh scans, added read-only troubleshooting and readiness proof boundary |
| Development, Validation, CI | Added documentation/CI checks, software vs data versions, precise later-verification limits and coherent-baseline release review |
| Migration, Docker retirement | Added current compatibility boundary; retained dated migration/retirement evidence and native-only setup |
| Daily indicators, Historical options | Reviewed method/source/availability limits; retained dated formulas/delivery facts without asserting current coverage |
| BRAIN values | Added explicit supersession for old HKG-pending wording: those markets are excluded |
| v0.2 design | Reviewed proposal/implementation separation; retained earlier dated review and publication records |
| Evidence access | Added canonical public/private evidence and version boundaries; repaired absent private-file links in publication docs |
| Dated reports, requirements and archive | Inventoried and retained unchanged; no market requests or reconstructed evidence |

Native reference-rate and extended receipt-membership documentation does not imply
those later source capabilities exist in the older publication tree. The public
catalog/provenance reference remains specific to its own code. No private requirement
files, source credentials, account rows or live data were added to public source.

## Checks and limits

`make docs-check` checks canonical relative paths and Markdown heading anchors,
exact catalog mapping rows, package/lock/version agreement and every CLI subcommand's
help parser against an absent temporary runtime. It does not execute ingestion,
publication or broker access. Its link parser covers ordinary inline Markdown
links used by these guides; it is not a general Markdown renderer. Fenced examples
are ignored. Absolute paths, remote URLs and historical evidence availability
are not treated as portable checkout links. The native requirements index is also checked when present. Historical review
reports from October 21/31 and other nested private requirements/archive are
outside the canonical link gate; their inventory/disposition is explicit.

Meaningful regressions test broken relative targets/anchors, duplicate headings,
ignored fenced examples and catalog/version drift. CI adds the same docs check
after native setup. Full isolated unit suites and builds validate software only;
no live worker, network health, entitlement, upstream coverage, strategy economics
or consumer acceptance was tested. External provider documentation was not fetched
for this offline audit; source/vendor availability claims remain bounded by their
dated evidence and must be freshly checked before acquisition decisions.

## Publication handoff

The serialized publisher receives the final documentation commit, tree and changed
path manifest. This task does not launch a browser publisher, alter credentials or
claim hosted CI for an unpushed tree. Earlier write-auth failures are dated evidence;
the publication owner must establish the actual final branch and exact-SHA CI result.
Local verification results are recorded separately from that final acceptance.

Local command outcomes: [verification record](validation/docs-audit-20261107/checks.json).


## Second final review

A second review found the catalog gate could detect missing/currently changed rows
but allowed an extra obsolete dataset row when all real rows were also present.
The gate now checks the bounded catalog-mapping section for stale/mismatched and
duplicate rows as well; a regression demonstrates rejection of both cases.
The publication manifest was also expanded from the documentation-only delta to
all task changes since the verified published source baseline, with explicit
native-only dispositions. These corrections prevent silently incomplete handoffs.

The original inventory remains the initial audit snapshot at publication-source
commit 2da35e5. [Second-review evidence](validation/docs-audit-20261107/second-review.json)
records this follow-up without rewriting the earlier check results.
