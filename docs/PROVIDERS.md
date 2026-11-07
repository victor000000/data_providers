# Provider and ingestion reference

This map locates implementation and source limits. It describes local code,
reviewed against source on 2026-11-07; it is not a current vendor entitlement matrix or permission
to start a new backfill. Provider plans, prices and availability require fresh
verification when an acquisition decision depends on them.

## Source entrypoints

Paths in this table are relative to `data_providers/`. Open the linked module
before using job-specific flags; only the shared CLI has a stable reference here.

| Source / family | Main implementation | Role and interpretation |
| --- | --- | --- |
| IBKR TWS/Gateway | [providers/tws.py](../data_providers/providers/tws.py), [bounded history](../data_providers/providers/ibkr_bounded_history.py), [error classification](../data_providers/providers/ibkr_history_errors.py) | Contract qualification, broker observations and bounded history; API readiness is separate from historical transfer/entitlement |
| Theta | [terminal client](../data_providers/providers/thetadata.py), [history receipts](../data_providers/providers/theta_history.py), [scheduler](../data_providers/providers/theta_scheduler.py) | Retained EOD/history, option quote and Greek observations; sampling, vendor model and availability clocks stay explicit |
| Massive | [equity/options client](../data_providers/providers/massive.py), [futures client](../data_providers/providers/massive_futures.py) | Provider-native stock/option bars and futures catalogs/session bars; trade bars are not NBBO |
| Yahoo | [session](../data_providers/providers/yahoo_session.py), [warehouse](../data_providers/warehouse/) | US universe/price pipeline with raw and adjusted fields; research must choose its basis |
| Nasdaq | [providers/nasdaq.py](../data_providers/providers/nasdaq.py) | Supplemental source behavior used by warehouse jobs; keep source provenance |
| Alpaca | [HTTP helper](../data_providers/providers/alpaca_http.py), [fill-minute job](../data_providers/jobs/data_ops/download_minute_bars.py) | US underlying/option fill-window tapes; source stage must be merged before publication |
| Cboe/CFE | [futures archive adapter](../data_providers/providers/cboe_futures.py), [official settlement import](../data_providers/jobs/history/import_cboe_settlements.py) | Distinct futures, futures-option and index-option settlement datasets; settlement is not an execution or ordinary close |
| CN exchange/AKShare jobs | [CN schema](../data_providers/data/schema/cn.py), [exchange fetch](../data_providers/jobs/data_ops/fetch_cn_products.py), [CZCE adapter](../data_providers/providers/czce.py) | Venue-specific daily/settlement/IV collection; missing source fields stay null |
| Sina CN | [futures adapter](../data_providers/providers/sina_futures.py), [live chain](../data_providers/providers/cn_live_chain.py) | Current contracts/chains and rolling underlying history; capture clock does not establish source trading time |
| Broker exports/journals | [trading jobs](../data_providers/jobs/trades/), [snapshot schema](../data_providers/data/schema/broker_snapshots.py) | Immutable source receipts, normalized fills and dated snapshots; overlapping exports do not add executions |

The `interactive_*` modules expose bounded interactive source access. Their
presence does not authorize bulk history or change market scope. Futu readiness
and account adapters likewise do not authorize HK market acquisition.

## Instrument identity before requests

Use [data/symbols.py](../data_providers/data/symbols.py) and the configured runtime's
`plans/symbol_map.csv`. US canonical spelling follows IBKR. Given canonical input,
call `for_source(canonical, source)`; given another vendor's native input, call
`translate(native_name, source, target)` and retain both the returned canonical
identity and target spelling.

```python
from data_providers.data.symbols import for_source, translate

massive_ticker = for_source("BRK B", "massive")
canonical, theta_root = translate("BRK.B", "massive", "thetadata_option")
```

The configured map must contain these entries; unknown mappings raise
`SymbolError` before I/O. A stock ticker and option root use different source
names. `canonical_for` is a legacy reverse lookup that can return `None`, and
`occ_root` has compatibility fallback behavior; neither is a substitute for strict
new request resolution. Do not turn a failed translation into a guessed symbol.

`MA` names the US equity in the US namespace; `CN:CZCE:MA` names methanol.
Keep venue-qualified CN keys throughout joins and storage. Actual futures need
venue/local-symbol/full-expiry identity; FOPs retain native broker contract IDs.
Chain strike/expiry arrays do not prove that every Cartesian combination exists.
Source-native and historical contract IDs must not be silently rewritten when
current issuer grouping or ticker spellings change.

## Shared ingestion lifecycle

1. Define a scoped allocation and audit retained/overlapping requests.
2. Resolve identity and verify required source inputs before HTTP/broker I/O.
3. Preserve request parameters, outcome, raw bytes/checksum and retrieval time.
4. Normalize into source staging with typed identities, source tags and clocks.
5. Validate prices, units, keys and coverage; record empty/denied/invalid outcomes.
6. Merge transactionally with exact selected-stage inclusion verification.
7. Publish through the existing finalizer and prove inclusion in a verified release.

Provider-specific code may differ internally. The required evidence boundaries
remain the same. `jobs/` is provider ingestion despite historical subdirectory
names; consumer risk and strategy logic belongs in consumer repositories.

## Method and clock boundaries

| Observation | Preserve separately |
| --- | --- |
| Raw vs adjusted prices | Source OHLC vs `adj_*`; adjustment is not point-in-time knowledge |
| IV | Broker underlying 30-day, vendor contract IV and derived ATM/tenor series |
| Greeks | Source, native units, sampling interval and model version; unsupported fields remain null |
| Quotes | Requested cutoff, source quote clock, capture time and unknown historical availability |
| Futures | Session start, trading/session-end date, settlement and last-trade close |
| Snapshots | Source-local label, known timezone/event time, ingestion time and completeness flags |

[Data provenance](DATA_PROVENANCE.md) defines tagging and unlinked legacy records.
[Historical options](HISTORICAL_OPTIONS.md) and [Daily indicators](DAILY_INDICATORS.md)
describe specific delivered methods and formulas. Data integrity does not resolve
method equivalence, executable NBBO or unknown historical availability.

## Failure and concurrency policy

Use actual provider error text and retained outcomes. A transport failure is not
a successful empty response; a no-data response alone does not prove an instrument
was never listed. Retain incomplete downloads and immutable failed/uncertain keys.

Theta's receipt-folder guard, opt-in durable global exact-key claims, account slots,
legacy exclusive-worker gate and semantic overlap audit have different scopes.
Do not infer one from another. Default history clients are serial; bounded parallel
mode shares account admission and preserves one session per worker thread.
Four-slot opt-in prerequisites, pacing and recovery are documented in
[Native environment](NATIVE.md) and [Operations](OPERATIONS.md). Never restart a
healthy worker solely to change concurrency, and never add adapters to bypass an
account-wide limit. Admission freeze leaves queued unadmitted keys untouched while
already admitted calls finish and retain receipts.

`HistoryClient` also retains `option/list/expirations` responses through the same
receipt, claim and slot controls. Resolve each requested root with
`for_source(canonical, 'thetadata_option')` first. The returned native symbol and
expiration dates are provider metadata, not proof of quotes or IV on a historical
session. Keep this inventory separate from contract observations; reuse retained
receipts and audit semantic overlap before requesting it. Source contract:
[Theta v3 expirations](https://docs.thetadata.us/operations/option_list_expirations.html).

CN rolling underlying minutes can expire upstream. True CN option-minute history
remains an upstream limitation. Do not fabricate minute data from daily bars,
index volume from prices, or fills from position/snapshot differences.
