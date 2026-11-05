# Documentation reference expansion — 2026-10-31

## Scope

Expanded the project documentation for future operation, development and consumer
integration. This review concerns local implementation and documentation, not a
new maintenance run, vendor entitlement audit or source coverage claim.

## Changes

- Added configuration precedence, native wrapper defaults, shared variables and
  explicit source-local timestamp exceptions.
- Added the complete CLI/HTTP reference, including all maintenance scopes,
  publication defaults, error responses and interface-specific filters.
- Documented all 52 logical datasets with physical stores/tables, time/symbol
  routing and private-access requirements, derived from `catalog.py`.
- Added provider/source entrypoints, strict symbol-resolution guidance, retained
  request outcomes and source/method/clock boundaries.
- Added a durable handover checklist, recovery decision table and evidence template.
- Expanded development change recipes and documentation ownership mapping.
- Updated root/index navigation; corrected stale container retention instructions,
  universal-year partition wording and incomplete HTTP endpoint descriptions.

## Source basis

Checked the local CLI, SDK, HTTP server, runtime/configuration, native/setup
scripts, maintenance supervisor, catalog, publication implementation, symbol
resolver and representative adapters. Existing dated requirements, archives,
raw receipts and operational worker scripts remain preserved in place.

## Verification

Completed checks:

- 12 changed/added Markdown files checked for balanced code fences.
- All 149 relative local links in those files resolve.
- All 52 catalog entries exactly match the documented physical table, time field,
  symbol field and privacy routing.
- All nine subcommand help paths passed.
- Dataset listing, trading-plan preview and derivatives-plan preview passed against
  an explicitly absent temporary runtime; none created that runtime.
- Focused isolated tests passed: **21 passed** in `test_delivery.py`,
  `test_runtime_paths.py`, `test_native_environment.py` and `test_maintenance.py`.

The full test suite, package build and consumer gate were not run for this
documentation-only change. No source compatibility behavior was changed. No production acquisition, import, merge, publication,
broker probe or service restart is part of this documentation task.
