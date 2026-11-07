# Historical option research delivery — 2026-10-18

This page preserves dated delivery/method evidence. For current acquisition scope,
see requirements (private path `docs/requirements/README.md`; [access boundaries](EVIDENCE.md#private-evidence)); for live status, see
[Operations](OPERATIONS.md). Counts and “current” results below refer to their
recorded run, not a continuously refreshed coverage claim.

## QC reference eligibility update

The QC owner reports that full run
`strangle_basket_20261018_092946` failed on strategy margin-call accounting and
rejected rolls. Its interim performance and execution artifacts are **not a
validated provider comparison reference**. The saved progress JSON still says
running/queued and is stale relative to this owner report. A repaired run needs
independent validation before it can be accepted as a new reference. The older
permitted immutable execution-tape inputs described below retain their existing
limited comparison purpose; they do not establish historical quote coverage.

The current requirement resend is byte-identical to
`requirements/QC_SHORT_STRANGLE_UPDATE_20261018_092610.md`, SHA256
`6f2868e3c004c24dd82cee0f8950e55d11933a7e26ed2141ad8ec17d292fb364`.
The 504 completed comparable-IV observations, actual timestamped bid/ask,
standard monthly identification, delivery-qualified futures metadata and
known-as-of earnings remain provider input requirements. Strategy accounting,
margin handling, batch sequencing and rejected-order recovery remain in QC.
This receipt does not change the documented quote entitlement blockers.

Owner: data_providers. Consumer requirements are preserved in
QC requirement (private path `docs/requirements/QC_HISTORICAL_OPTIONS_20261018.md`; [access boundaries](EVIDENCE.md#private-evidence)) and
screener IV request (private path `docs/requirements/SCREENER_IV_HISTORY_20261018.md`; [access boundaries](EVIDENCE.md#private-evidence)).

## Current result and blockers

The bounded SPY run is **blocked for executable quote validation**, not a completed
NBBO sample. The authenticated Theta Terminal is version 20261014:a03167c;
its 2026-10-18 login reports Stock FREE / Options FREE / Index FREE / Rate FREE,
with one concurrent request. Existing Windows endpoint was unavailable; the
project-owned bundled terminal was restored inside data_providers. Its launcher
now puts its Java runtime on PATH, which the bootstrap requires for its child JVM.
No account purchase, upgrade or strategy execution was performed.

Actual endpoint checks for SPY 2024-04-12 / expiry 2024-05-17 / 486P:

| Kind | Actual result | Delivery status |
| --- | --- | --- |
| One-minute NBBO | Theta 403 subscription refusal | not_entitled; 0 rows |
| Historical quoted-contract inventory | Theta 403 | not_entitled; 0 rows |
| Daily OI | Theta 403 | not_entitled; 0 rows |
| EOD Greeks/IV, methodology version 1 | Theta 403 | not_entitled; 0 rows |
| SPY SIP minute OHLC | Theta 403 | not_entitled; 0 rows |
| SPY EOD | Theta 200 | 18 validated session rows in the bounded sample |
| SPX index minute observations | Theta 403 | not_entitled; 0 rows |
| Alternative Massive option quotes | 403 NOT_AUTHORIZED | not_entitled |
| Probed Alpaca historical option-quote route | 404 | endpoint unavailable; no quote coverage claim |

All vendor refusals and successful bodies are retained, hashed and associated with
request parameters. A refusal is never recorded as an empty successful dataset.
The 36 contract-days over 18 sessions come solely from the permitted execution tape.
The 570 executions are compared in `execution-comparison.json`; unavailable quote
fields remain null and execution eligibility is false. This does not reproduce
restricted QC reference data or validate cross-feed price equivalence.

## First dates and IV warmup

Live date-list responses establish that SPY options expiring 2012-06-16 have quote
catalog dates 2012-06-01 through 2012-06-15. Actual quote payload access is still
refused. SPY 2024-05-17 lists 2024-01-10 through 2024-05-17. The SPY stock quote
catalog separately begins 2017-01-03 and ends 2026-10-16; this is a different
asset-class coverage boundary. The January 3, 2012 stock EOD request returned 403.
A catalog date is not a licensed, downloaded price observation.

Theta advertises OPRA since June 2012; January–May 2012 remains outside that
advertised range. First usable executable backtest date and first complete
504-observation comparable-IV window are **unknown/blocked**, not January 2012.
At 2026-09-30 the existing SPY series has 501 positive finite distinct IBKR 30-day
IV dates and 359 Alpaca ATM dates. They must not be combined. Neither source
establishes the requested 45-DTE methodology or historical availability timestamps.
The existing 513 IBKR dates through October 16 do not cure the requested cutoff,
tenor or point-in-time gaps. No IVR percentile substitution is made here.

Theta does not currently provide CME futures data, and options access does not
include index-underlying access. Existing independently sourced futures daily
coverage is recorded per root in `existing-coverage.json` (32 roots in the checked
release). The requested RB/HO roots are absent from that published daily inventory;
CL, NG, ZN, ZT, ZB, ZC, ZS, ZW, NQ, ES, HG, GC and SI are present for limited recent
histories. VIX option settlement/trade history is separate from CME futures-option
quotes. Gateway historical FOP access remains an unresolved gap. Current catalog
discovery and downloaded price coverage remain separate; the healthy broad US
worker and existing completion waiters were left running.

## Schemas and timestamp rules

All new datasets use the existing market_history.duckdb and the existing immutable
Parquet lake. No consumer-owned canonical warehouse is introduced.

| MarketData dataset | Table | Contract |
| --- | --- | --- |
| us_option_quotes | option_nbbo | NBBO bid/ask, sizes, codes, interval_ts, nullable quote_ts and age |
| us_option_contract_history | option_contract_history | Session-specific quoted inventory; explicit unknown contract terms |
| us_option_research_observations | option_research_observations | Separate OI / IV / Greeks kinds, model and raw inputs |
| us_option_reference_bars | option_reference_bars | Source-labelled MIDPOINT OHLC with nominal frequency, interval-end basis, capture time and raw receipt hash; not trades or NBBO |
| us_underlying_research_bars | underlying_research_bars | Raw minute OHLCV, start/end boundaries, volume in shares |
| us_underlying_research_daily | underlying_research_daily | Provider EOD, raw fields, explicit extended-session scope |
| research_coverage | research_coverage | Contract/session/kind status and raw-body receipt hash |

Timestamps are TIMESTAMPTZ in UTC; source_timezone is America/New_York. Theta's
one-minute quote response is last NBBO at the interval timestamp. It does not
establish the originating update time: quote_ts, quote_age_ms and available_at
remain null. Unknown age is never zero age. Negative/nonfinite prices are
quarantined; locked, crossed and one-sided quotes are retained with quality flags
and excluded from executable-price interpretation. With zero delivered quotes,
quote-age distribution is null and the flag counts are explicitly zero.

Underlying minute bars are stamped at interval opening time; interval_end is one
minute later and is the SDK's time filter. Do not join a not-yet-completed bar to
a decision. The free EOD sample includes post-close observations (created around
17:15–17:17 New York time), so it is neither a noon decision input nor an RTH close
substitute. It remains separate from market_data/yahoo raw and adjusted series.

Observation time, retrieval time and availability time are different fields.
Unknown historical availability stays null. The inventory endpoint only proves
that a contract was quoted on that session, not its listing-time availability.
Multiplier, tick, exercise/settlement terms, adjusted deliverables and series type
remain null unless verified from an authoritative contract source; the QC tape's
100 multiplier is a comparison input, not provider metadata. Futures delivery
IDs must be supplied by a supported futures source, never inferred from OPRA.

## Read-only example

```python
from data_providers import MarketData
from data_providers.lake import connect_store
m = MarketData(backend="parquet")
rows = m.read("us_option_quotes", symbols=["SPY"],
    contract_ids=["US:OPRA:SPY:2024-05-17:P:486"],
    start="2024-04-12T13:30:00Z", end="2024-04-12T20:15:00Z")
# Current result is []; inspect coverage to distinguish a blocker from no trading.
coverage = m.read("research_coverage", symbols=["SPY"], start="2024-04-12", end="2024-04-12")
with connect_store(m.home, "history", release=m.release.id) as con:
    result = con.execute("""
      SELECT q.contract_id,q.interval_ts,q.bid,q.ask,q.quote_age_ms,
             u.interval_end,u.close,c.expiry,c.strike,c.multiplier
      FROM option_nbbo q
      LEFT JOIN option_contract_history c ON q.source=c.source
        AND q.contract_id=c.contract_id AND q.session_date=c.session_date
      ASOF LEFT JOIN underlying_research_bars u
        ON q.source=u.source AND q.underlying=u.symbol
        AND q.session_date=u.session_date AND q.interval_ts>=u.interval_end
      WHERE q.quality='two_sided_age_unknown'
    """).fetchall()
# Exploratory alignment only. A strict execution/as-of policy must additionally
# require known availability <= decision, known quote age and verified terms.
```

HTTP /v1/data also accepts repeated `contract_id` filters. SQL filters on exact
contract IDs and UTC time ranges pass through the existing DuckDB Parquet reader.
No arbitrary fill-forward is performed. Existing trades/bars/IV datasets retain
their source names and semantics.

## Scope, estimates and resumption

Collector: `python -m data_providers.jobs.history.maintain_option_research_sample
--execution-tape <permitted-tape.csv> --base-url http://127.0.0.1:25503/v3`.
The run refuses more than 36 contract-days. Per-request checkpoints and immutable
raw bodies prevent repeated successful/refused downloads; transient failures
remain retryable. Requests are serialized with a shared Theta research lock;
merges take the maintenance writer lock, verify exact rows transactionally and
retain staging. The current implementation covers the validated free EOD path;
paid quote/OI/Greeks paths remain live-unvalidated because access was refused.

The requested sample would need 36 full-session quote requests and at most 14,616
minute-boundary snapshots, roughly 4.4 MB at an illustrative 300 bytes/row before
compression. Underlying, inventory, OI and Greeks add separate requests. Broad
history is estimated as listed contract-days * session minute boundaries; the
actual inventory cannot be measured under current entitlement. For scale context
only, 100 underlyings * 100 contracts * 504 sessions * 391 boundaries is about
1.97 billion rows / 591 GB uncompressed, excluding indices, futures, tick detail,
OI, Greeks, provenance and corporate actions. This is a scenario, not a download
proposal or measured compression ratio. No all-market download was started.

Remaining prerequisites: licensed option quotes and metadata, stock-minute and
index access separately; reliable PIT earnings revisions, market cap, calendars,
corporate actions and historical contract specifications; comparable IV models
and availability. Full candidate grids, management sessions, early closes and
historical listings require validation after entitlement is available. They are
not implied by this small execution-tape sample.

## Evidence

Canonical run folder: `runtime/var/maintenance/theta-spy-20261018/`.
`report.json` carries publication verification; `capabilities.json`, `coverage.json`,
`first-dates.json`, `quote-quality.json`, `existing-coverage.json`,
`execution-comparison.json`, `quarantine.json`, `alternatives/report.json` and
raw receipt bodies preserve the audit trail. Per-partition hashes live in the
immutable lake manifest. Consumer verification is added alongside these files.

Official sources checked 2026-10-18:

- [Theta quote interval semantics](https://thetadata.net/docs/operations/option_history_quote.html)
- [Theta OPRA history](https://www.thetadata.net/options-data)
- [Theta futures/index access limitations](https://www.thetadata.net/faq)
- [Theta OI publication and previous-session semantics](https://thetadata.net/docs/operations/option_history_open_interest.html)
- [Theta Greeks model and EOD timing](https://thetadata.net/docs/operations/option_history_greeks_eod.html)
- [Massive option-quote entitlement and history boundary](https://massive.com/docs/rest/options/trades-quotes/quotes)
- [Alpaca historical option coverage](https://docs.alpaca.markets/us/docs/historical-option-data)


## Current-chat requirement resends — 2026-10-18

Both consumer resends are recorded. The qc_work specification now includes
60,000 USD initial equity, equity/x collateral budgets only for new openings,
unchanged existing quantities, and reconfirmed approximately 45-DTE entry /
mandatory 21-DTE exit. These remain consumer-owned rules.

Rechecked immutable release `20261018T010604Z_e4064ee0` through read-only queries.
Both qc_work's real interpreter and the screener container again passed the
MarketData query, coverage-status, refreshed IV count, SQL alignment and Parquet
checksum checks. There are 18 research daily observations and 162 coverage
receipts; quotes, contract inventory, research Greeks/OI and research minute
bars remain empty. The executable quote sample remains blocked. Saved capability
response bodies and the raw ZC source file passed hash checks. Same-day access
receipts were reused; no duplicate downloads or broad ingestion were started,
and active maintenance workers were preserved.

IBKR distinct sessions remain DRAM 135, MUU 130, SPCX 87 and MDLN 207, all through
October 16 and all partial for the screener's 252-session requirement. MUU's
pre-April IV availability and authoritative calendar missing-session counts
remain unresolved. The consumer's 272-candidate screen was not rerun here.

ZC horizon audit at October 16, using half-open calendar windows:

| Horizon | Distinct usable sessions | Range status |
| --- | ---: | --- |
| Full available history, from 2020-06-30 | 1,527 | nonflat |
| 1Y | 242 | flat at 0.5393; IVR undefined |
| 2Y | 485 | flat at 0.5393; IVR undefined |
| 5Y | 1,212 | nonflat |
| 10Y | 1,527 | nonflat; history begins in 2020 |

Longer nonflat ranges do not prove current activity or fresh observations.
Original source repetition/inactivity evidence and tenor/availability limitations
still apply. No ranks, source splicing or synthetic observations were introduced.

Current audit and consumer results are retained under
`runtime/var/maintenance/consumer-resend-20261018/` as `audit.json` and
`consumer-verification.json`. The detailed read-only query example and actionable
access, metadata and warmup gaps above remain applicable. No package code changed;
verification used actual consumer read paths without a maintenance/download cycle.

## QC CL consumer correction — October 18 follow-up

QC reports a validated, source-session-limited premium-unit repair for its own
LO/CL inputs on December 21–24, 2015; normal units resume December 28. The factor
0.01 applies only to affected QC option premiums. It does not apply to provider
raw data, futures prices, strikes, multipliers, quantities, OI or volume.

The 188-sample survey and exact failed-cycle regression are consumer evidence,
not an upstream acknowledgment or an independent provider quote comparison.
Regression `23e74f967f039362598ab61ff2652247` passed ten fills, three rolls,
a 21-DTE exit, no residual holdings, and Rust/Python/account-ledger checks.
Its +145.30 USD engineering fixture result is not full baseline performance.
The original failed baseline remains invalid. Corrected full baseline
`8257ae601fe9ccb8918762562e5663ac` is running; audited completion remains pending.

Provider follow-up: retain independent delivery-qualified quote/unit validation
as open. Do not propagate this QC-specific correction to any provider series.

Source receipts:

- `/home/vic/data_providers/docs/requirements/QC_CL_OPTION_SCALE_RESOLUTION_20261018.json`, SHA256 `da568a739d9d6fbaf844cfc3c3b0841885d2dd0a664a169008838067bea4fd9b`.

- `/home/vic/qc_work/strategies/sstrangle/results/cl_scale_fix_20261018/summary.json`, SHA256 `0da86dc6ff955f7d7644f3500e4c500e622a2bceb4b0a29d0a19b2143362e6c0`.

### Corrected full-run status — October 18, 08:09 UTC review

Full execution `8257ae601fe9ccb8918762562e5663ac` is now failed and invalid
as a baseline. QC reports the bounded CL repair passed in this run, followed by
a separate ZW paired buy-to-close buying-power rejection on August 4, 2017.
Both five-lot closing orders received zero fills. Consumer evidence retains
726 orders and 724 fills, matching Rust/Python and account reconciliation.
No quote-scale defect is established for this ZW event, and there is no new
provider normalization request. Execution/group buying-power reproduction is
consumer-owned; independent provider quote validation remains open.

Source: `/home/vic/qc_work/strategies/sstrangle/results/strangle_basket_20261018_145612/failure_diagnostics/summary.json`, SHA256 `9b5de62c591935bdd042df17312aef3c16122a7adc4ee2f1ba929debc7be5d02`.

### ZW consumer exit regression resolved — October 18, 09:39 UTC review

Consumer regression `9c639f004d7a030d8ba144dee7704df0` now passes the
first eligible 21-DTE exit, all ten actual fills, full quantities, default
margin models, no residual holdings, and Rust/Python/fees/account audits.
This is an engineering fixture, not validated full-baseline performance.
The original failed full run remains invalid. Replacement full baseline
`adeeabfd0f50d7a5035652e73f872965` is queued, with audited results pending.
No new provider data or scaling change is requested by this consumer repair.

Source: `/home/vic/qc_work/strategies/sstrangle/results/zw_exit_fix_20261018/corrected_summary.json`, SHA256 `0d0213dff4a3c6b2b5d52a5aa38df319ec47f5d35fdb5227f00a8a1a662f8e34`.

### Consumer-audited terminal baseline — October 18, 12:39 UTC review

QC owner published an audited baseline for execution
`63b8c3950a6da5ff54d25a8c8209470f`, requested 2012–2026 but terminating on
March 12, 2020 due to insolvency. The report records 145 closed strangles,
1,010 genuine fills, 3,405.54 USD fees and terminal equity -42.29 USD from
60,000 USD, with Rust/Python, accounting and no-residual-position checks.
These are consumer-audited results for the frozen configuration and QC source
coverage, not an independent provider quote validation or observations through
2026. No post-insolvency performance is supplied. The bounded QC CL unit repair
remains source/session-specific. Independent local quote coverage is still open.

Source: `/home/vic/qc_work/strategies/sstrangle/results/strangle_basket_20261018_191012/baseline_report/REPORT.md`, SHA256 `9c1212b34d29b20667547ee0df4dde08eee2da16544e09fb56bff12a15df0645`.
