# Market-data maintenance

Scope: US and mainland CN PV/listed options plus supporting reference data and
independent broker/journal maintenance. See [active scope](requirements/MARKET_DATA_SCOPE_20261019.json).
Before executing, inspect [Operations](OPERATIONS.md) for active workers and saved
checkpoints. Commands below run on the host using the [native environment](NATIVE.md).

## Trading records

For each user request to maintain trading logs, run this routine on the Linux/WSL
host through the native environment. It rescans the authoritative mounted folder every time, even if the
previous check found no changes:

```bash
./scripts/native.sh data-providers maintain --scope trades --execute --publish
```

The initial `scan_inputs` step records current paths, sizes, modification times
and SHA256 hashes, added/changed/removed files, and newest-modified exports per
type in `runtime/var/maintenance/trading-inputs/latest.json`, retaining a dated
snapshot for each invocation. It hashes contents again even when size and mtime
are unchanged. Subsequent receipt capture, journal/broker loaders and fill-minute
capture use the configured source; unknown files are inventoried, not invented
executions. Source-scan failure blocks trading loaders and broker refreshes and
prevents successful publication. This routine does not schedule new runs itself.

## Market plans and ingestion behavior

The maintained scope includes the US stock list, the union of the current
ETF311/ETF500 source rosters, all `FUT:` roots in the futures universe, and the
CN product map. On 2026-10-17 these cover 644 distinct ETFs, 18 US futures roots,
and 35 CN option products. CFFEX MO remains explicitly an index option.

```bash
# Run on the host from /home/vic/data_providers.
./scripts/native.sh data-providers maintain --with-bars --with-settle --futures-full
# Inspect derivatives plus the standard trading-log refresh:
./scripts/native.sh data-providers maintain --scope derivatives --with-settle --futures-full
```

These examples print plans. `--execute` runs the selected plan; `--publish` publishes
only after successful execution. All scopes include trading-input/receipt/broker/
journal steps. Expensive flags are not the default recovery procedure. The generic
plan can include catalog backfills; inspect it before executing and do not overlap
existing workers. Completed priority work is not restarted by this documentation.

ETF scope is rebuilt from reference data. It does not inherit strategy/liquidity
filters from the 413-symbol stock download list. ETF option bars use the existing
monthly-expiry/strike-band policy from February 2024, subject to provider availability;
ATM IV uses 24 months and ATM ±7 strikes. This does not assert every weekly expiry
or every strike is covered. Empty responses are not proof of non-optionability.

US futures use separate typed tables in the existing `market_history.duckdb`:
`us_futures_contracts`, `us_futures_daily`, `us_futures_option_chains`, and
`us_futures_option_daily`. An equity such as CL or ES cannot be mistaken for a
future in these datasets. Future IDs include venue, local symbol and full expiry;
FOP IDs retain the IBKR contract ID. Pricing and metadata join on those IDs.
Massive session-end dates determine daily trading dates; UTC session starts and
settlement prices remain separate from dates and closing prices.

US maintenance discovers all returned futures definitions and option-chain ladders.
A chain's strike and expiry arrays are not treated as a Cartesian list of confirmed
contracts. Full FOP backfill qualifies actual contracts per class/expiry before
requesting history. Provider-year ambiguity in distant futures is reported rather
than assigning a nearer contract's prices to a distant expiry.

`--futures-full` backfills the discovered futures contracts over the existing
two-year window. Without it, routine maintenance refreshes front contracts and
chain metadata. Historical backfills can take hours under provider pacing limits.
For that specific `maintain_us_futures` job, a stopped run supports the following
resume interface. Replace `RUN_ID` with its existing receipt-backed run ID; this is
not the Massive catalog or the current frozen ten-year IV worker:

```bash
./scripts/native.sh python -m data_providers.jobs.history.maintain_us_futures --full --resume RUN_ID
```

CN maintenance retains its exchange EOD/settlement/IV jobs. The additional chain
collector captures every returned strike and source-listed contract month;
`--with-settle` also probes the preceding 24 delivery months. Returned contract
identities must match both product and requested month. Guessed month candidates
are never called listed contracts unless a source actually returns them.
A full-strike history job backfills missing daily bars from that captured inventory.
Original HTTP responses and retryable per-contract checks are retained.

`cn_option_quotes` contains observations, not fabricated daily bars. Its UTC capture
time is not the price's trading date. The historical job retains missing expiry,
OI, settlement, IV and Greeks as NULL when the source does not provide them or a
verified reference expiry cannot be found. Continuous futures remain distinguishable
from deliverable contract-month series by contract identity and source.

All new jobs write retained staging under `runtime/staging/futures/`. Verified
transactional merges check that every staged row is represented in its destination.
Exchange maintenance and snapshot publication coordinate through the maintenance
lock; source downloads can proceed independently of consumers' Parquet reads.

Coverage and failures are available in `futures_ingestion_checks` and
`cn_option_history_checks`, and run receipts under `runtime/var/maintenance/`.
Publication validates data integrity; it does not imply complete upstream coverage.
A failed supervised run does not automatically publish. After recovery and audits,
`data-providers --private lake publish` explicitly publishes the verified available state.

## Source limits

Gateway contract definitions and account APIs can work while historical requests
time out. A valid ES FOP request on 2026-10-17 still timed out after 75 seconds.
Those failures are recorded, not converted into empty successful history.
[IBKR historical-data limitations](https://www.interactivebrokers.com/docs/tws-api/doc/md/introduction)
also exclude expired-option history. This was a dated capability observation; current access must be established from
actual receipts, not inferred from a connected API. The project cannot reconstruct that archive
from current chains or daily futures prices.

The Massive futures client uses the configured credential and a shared five-per-minute
budget. It preserves server responses and follows HTTPS pagination only.
[Massive's session-bar specification](https://massive.com/docs/rest/futures/aggregates)
explains the distinction between session start, session-end date, last trade and settlement.
CN venue/source gaps and stale listings, including ZC, remain visible in the coverage report.

## Provider catalog expansion and volatility products

The earlier 18-root US plan and 35-row CN options map are initial scopes, not
complete exchange universes. `maintain_catalogs --market us` walks the configured
Massive product and dated contract catalogs (3,352 product definitions discovered
on 2026-10-17), then fetches contract-specific session bars. Metadata, pagination
continuations and completed price requests are retained under
`runtime/var/maintenance/catalogs/`. Its five-request-per-minute shared budget
means a broad historical run can take days. Use `--resume RUN_ID` after interruption.
Do not equate discovered definitions with downloaded price coverage.

`maintain_catalogs --market cn` reads Sina's current product-node catalog
(86 nodes returned on 2026-10-17), validates returned deliverable contracts and
venues, fetches daily histories, and checks each product for actual option-month
and strike inventories. Returned SHFE-group SC/NR/LU/BC/EC identities retain INE
as their venue. Historical month candidates are requests, not assertions of listing.
A subsequent option-history pass uses only confirmed quote identities. Source
catalog omissions, stale nodes, missing chains and rejected bars remain gaps.

`maintain_vix` uses Cboe's exchange archives for VX futures and UX futures options,
including expired UX archives that Gateway cannot supply. The two-year run found
118 futures contracts and 45,630 UX series. Daily settlement, volume and OI are
available through `us_futures_option_settlements`; executed option OHLC remain in
`us_futures_option_daily`. A zero-volume source row with indicative ranges does
not become an executed OHLC bar. Underlying VX expiry and UX option expiry remain
separate, and original archive IDs and response bytes are retained. The job makes
verified intermediate merges so readers can receive available data while older
archives continue downloading. Index VIX options are a different instrument.

`maintain_catalogs --market cfe` additionally walks Cboe's non-VX futures archive
roster. Non-UX futures-option references are retained, with an explicit unsupported
normalization receipt until those product specifications are verified. ICE and
other exchanges outside configured feeds remain uncovered; a successful catalog
request is not a claim of complete US futures-option pricing.

`seed/volatility_etps.csv` keeps VXX/VXZ (ETNs) and UVXY/SVXY/VIXY/VIXM/UVIX/SVIX
(ETFs) in the maintained option universe regardless of strategy filters. Together
with the current ETF rosters this produces 647 distinct symbols in this run.
The unverified ticker `VXY` is not silently assigned to another product. New price
histories must be built into the warehouse before computing IV. Missing price
inputs are retryable failures, not evidence that listed options do not exist;
`download_atm_iv --retry-empty` permits recovery after repairing those inputs.

## Execution and journal rules

The canonical trading command at the top freshly hashes the mounted inputs on
each invocation. Do not rely on a previous inventory when the user requests
maintenance. Missing source blocks trading loaders/broker refreshes. Preserve
newest-modified originals per type, including extensionless CN exports identified
by headers. TWS executions come from native broker execution capture; positions
and journal changes never imply fills. Deduplicate execution IDs and overlapping
exports; source receipt counts are not execution counts.

Checked entitlement refusals remain visible blockers. Where an explicitly bounded
maintenance plan omits a known unchanged denied step, record that omission and its
receipt; do not describe it as a clean full-scope run or silently retry the refusal.

Use [Operations](OPERATIONS.md) for existing finalizers and recovery. The supervisor
records logs and `result.json` per run; `summary.json` records requested publication.
Standalone `--private lake publish` is an explicit post-merge operation, not a
replacement for acquisition/verification or permission to race another writer.
