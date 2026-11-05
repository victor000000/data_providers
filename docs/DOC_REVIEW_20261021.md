# Documentation review — 2026-10-21

Scope: review project documentation and Docker information against the local
implementation, preserving ongoing downloads, source requirements and raw evidence.

## Findings and revisions

| Finding | Revision |
| --- | --- |
| README mixed setup, API, delivery and operational history in 259 lines | Reduced to 71 lines with task navigation and ownership/scope essentials |
| No focused Docker guide | Added service/build targets, private interpreter, fresh versus existing setup, mounts, variables, broker networking, HTTP ports, lifecycle and troubleshooting |
| Runtime-base could be mistaken for a self-contained application image | Documented mounted environment versus optional source-build image and whole-checkout mount shadowing |
| `unless-stopped` could be mistaken for downloader supervision | Explained idle startup process and separate watcher/checkpoint recovery |
| Broad execute/backfill examples appeared as routine maintenance defaults | Converted generic examples to plan inspection; linked active-worker audit and bounded resume behavior |
| Current worker evidence was scattered across dated files and logs | Added Operations map for status, receipts, locks, finalizer and singleton recovery |
| Read-only SDK description omitted automatic published-release pinning | Added dedicated Data access guide with fallback/pinning and interpretation limits |
| Historical tests/gates/counts appeared to describe current checkout guarantees | Preserved October 17–18 validation in a dated archive; replaced front page with evidence semantics and dated references |
| Hundreds of requirement artifacts lacked a concise entry point | Added requirements task map and supersession/evidence rules without moving hashed originals |
| Specialized reports used undated “current” wording | Added dated-evidence banners and current scope/status links |
| Guide ownership was unclear | Added docs index and Development upkeep rules |

## Verification

- Read local CLI, runtime resolution, maintenance planner, Dockerfile, Compose,
  package metadata, Makefile, source adapters and reader behavior.
- `docker compose config --quiet` passed without changing the container.
- Container CLI checks passed: datasets 39 entries; trades plan 11 steps;
  derivatives/full-settlement plan 38 steps; full optional plan 48 steps.
  These were plan-only invocations, not downloads or trading refreshes.
- CLI help for maintain, lake, export and serve passed.
- Local Markdown file targets and anchors checked across the root and primary
  docs/requirements index. No broken local references remain after this review.
- Documentation edits do not change application code, image configuration, running
  worker commands, raw receipts or dated requirement contents. No container restart,
  acquisition cycle, order or consumer chat message was used to validate docs.

Test counts in historical reports remain dated evidence. This review does not
claim a new full provider test run, full consumer gate or validated strategy baseline.
