# Source-qualified US/CN daily calculation graph

This page preserves dated delivery/method evidence. For current acquisition scope,
see requirements (private path `docs/requirements/README.md`; [access boundaries](EVIDENCE.md#private-evidence)); for live status, see
[Operations](OPERATIONS.md). Counts and “current” results below refer to their
recorded run, not a continuously refreshed coverage claim.

## CN cash ETF extension — 2026-10-18

Published in **`20261018T022317Z_ce0c0a4a`**, with 460 verified Parquet partitions.
Data-container and actual QC-host SDK reads passed. BRAIN independently
recomputed all 43,050 indicator rows (42,070 values and 980 NULL cases) from
source data using Python statistics; all matched. This is partial acceptance of
the three local methods, not broad-market completion or exact BRAIN equivalence.
The independent report and its source hash are retained under
`runtime/var/maintenance/consumer-monitor/brain-provider-validation-latest.json`
and `brain-provider-validation-receipt.json`.

`CN:SSE:ETF:510050` now has receipted raw and qfq-adjusted history: **3,168
sessions per convention** from the requested October 2013 warmup window through
2026-10-16. The same three local calculation methods yield **9,321 non-NULL
values** over 3,107 dates from 2014-01-02. Total canonical calculation rows are
now 43,050, with 42,070 non-NULL values across the US/CN scopes below.

Raw inputs use the new `cn_equity_daily` MarketData dataset in `cn_market.duckdb`.
Raw and qfq observations have separate adjustment keys. Price geometry, exact
raw/adjusted date-set agreement, identity, finite values and source hashes are
verified before transactional merges. Successful receipts are reused on resume;
invalid HTTP-200 payloads do not count as successful history. An initial source
connection failure was retained and recovery reused already successful responses.

The source's numeric volume and turnover are preserved without guessing a share
scale or currency. Volume unit is `vendor_native_unverified`, currency and source
availability are NULL. The mean-volume field therefore also carries that unit;
it must not enter a five-million-share test. Returns and volatility use the qfq
snapshot, with historical adjustment availability and calendar continuity still
unknown. Strict PIT acceptance and exact BRAIN equivalence remain unestablished.

This calculation uses freshly hashed HTTP entity bodies, so its `input_release`
is NULL; full source receipt references/hashes are embedded in lineage. The raw
and calculated tables are published together in one output release. That is
distinct from the earlier graph's inputs from an already published lake release.

Receipt/report root: `runtime/var/maintenance/cn-etf-510050-20261018/`, with the
verified run at `run-20261018T021046783308Z/report.json`. Publication proof is
`.local/cn-etf-publication.json`. Reproduce through
`./scripts/native.sh python -m data_providers.jobs.history.maintain_cn_etf` on the host.
This is one CN ETF input, not broad CN equity coverage or an options archive.

```python
from data_providers import MarketData

m = MarketData(backend="parquet")
native = m.read("cn_equity_daily", symbols=["CN:SSE:ETF:510050"],
                start="2026-10-01", end="2026-10-16")
derived = m.read("daily_indicators", symbols=["CN:SSE:ETF:510050"],
                 start="2026-10-01", end="2026-10-16")
# Preserve each record's adjustment, units and quality when forming panels.
```

Source route/adjustment documentation:
[AKShare ETF history](https://akshare.akfamily.xyz/data/fund/fund_public.html).

## Initial US/CN futures calculation release

Published release: `20261018T020100Z_7ea8c073`, 446 verified Parquet partitions.
QC's actual host interpreter and the screener container both verified the SDK,
33,729 indicator rows / 32,749 non-NULL values, preserved futures-IV values and
2,046 catalog families containing all 3,352 original definition variants.

`daily_indicators` is a MarketData dataset in canonical `market_history.duckdb`
and the immutable Parquet lake. The first calculation run is
`runtime/var/maintenance/daily-pv-20261018T020011959461Z/report.json`, using input
release `20261018T015546Z_f666d450`. Source downloads are independent of these
read-only calculations; no new network history or database backup was created.

Method `observed_bar_pv_v1` partitions by market, venue, instrument and source:

| Field ID | Formula / timing | Units |
| --- | --- | --- |
| close_return_since_previous_observation | P(t) / P(previous observed date) - 1 | fraction |
| prior_20_observation_mean_volume | mean of exactly 20 preceding source bars, excluding current | shares for US, contracts for CN futures |
| realized_volatility_20_observation | sample standard deviation of 20 simple observed returns, ddof=1, multiplied by sqrt(252) | decimal annualized using 252 observations |

These names deliberately describe observations. Calendar continuity is not
verified, so a return is not automatically a consecutive-session daily return.
The volatility is historical realized volatility, never substituted for IV.
Missing inputs and incomplete warmup produce NULL with a reason; zero volatility
is preserved. No missing sessions are forward filled.

US inputs are SPY/QQQ vendor-adjusted close with raw source share volume, using
pre-2014 observations for warmup. The latest adjusted snapshot is not established
as a historically available corporate-action revision. US listing venue is
unknown in this calculation and remains `UNSPECIFIED_US`.

CN inputs are source-native DCE M individual futures contracts from `sina_contract`.
Four-digit delivery-month codes and venue qualify identities; each contract has
its own windows, with no continuous-contract or root-level roll splicing.
Source contract volume conventions are preserved rather than equated with shares.
CN equity/ETF inputs, more products and earlier expired contracts remain pending.
The first stored date below is not a claim of source or product inception.

| Market | Instruments | Rows, including NULL warmup | Actual non-NULL values | Stored output dates |
| --- | ---: | ---: | ---: | --- |
| US | 2 | 19,302 | 19,302 | 2014-01-02–2026-10-16 |
| CN | 24 delivery contracts | 14,427 | 13,447 | 2023-11-15–2026-10-16 |

Record identity includes market, venue, instrument, day, source, method and field.
Each record carries units, nullable historical `available_at`, calculation time,
input release, quality and JSON lineage. Lineage records the previous observed
date, price convention, sample-standard-deviation and annualization parameters.
Retrieval time is not invented from calculation time. All values currently have
unknown historical availability, so strict point-in-time eligibility remains
unestablished. These local methods do not assert exact BRAIN equivalence.

```python
from data_providers import MarketData
from data_providers.lake import connect_store

m = MarketData(backend="parquet", release="20261018T020100Z_7ea8c073")
us = m.read("daily_indicators", symbols=["US:EQUITY:SPY"],
            start="2026-10-01", end="2026-10-16")
cn = m.read("daily_indicators", symbols=["CN:DCE:FUT:M2701"],
            start="2026-10-01", end="2026-10-16")
with connect_store(m.home, "history", release=m.release.id) as c:
    panel = c.execute("""
        SELECT market, instrument_id, day, field_id, value, units, quality
        FROM daily_indicators
        WHERE method_id='observed_bar_pv_v1' AND day >= DATE '2026-10-01'
          AND field_id='prior_20_observation_mean_volume'
        ORDER BY market, instrument_id, day
    """).fetchall()
# Large selections use m.export('daily_indicators', output_directory, ...).
# Normal reads never download data, and retain the SDK's explicit row limit.
```

Existing aligned CN options primitives remain available through `cn_option_bars`
and SQL over `connect_store(m.home, 'cn', release=m.release.id)`: `venue`, `contract`,
`expiry`, `strike`, `call_put`, source and date identify each record alongside
OHLCV/OI/IV/delta. Missing expiry, terms or model fields remain unknown. An actual
dated chain query must preserve these identities rather than zip unrelated arrays.
Existing daily bars cannot stand in for NBBO or reconstruct intraday history.

Recalculate with `./scripts/native.sh python -m data_providers.jobs.history.calculate_daily_pv`
on the host. The job retains staging, validates source/date uniqueness, merges
transactionally under the maintenance writer lock and verifies every staged row.
Publication is a separate verified step. Tests cover hand-computed values,
exclusion of current volume, contract isolation, duplicates, NULL availability
and idempotent staging replay.
