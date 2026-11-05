# Data-source attribution and recheck contract

The source-tag requirement applies to all providers and dataset types: underlying
PV, actual futures, options, IV, Greeks, calendars, actions, specifications,
reference metadata and private broker records. Market-data acquisition and live
execution journals retain separate ownership and access controls.

Every observation must expose a nonempty `datasource_tag`. Source identities must
include a feed/method distinction where needed; a vendor name alone does not make
two series equivalent. Original provider symbols and immutable historical keys
remain intact. New receipts preserve native request parameters, raw bytes,
SHA-256, retrieval time and the original receipt location. Native source/event
clocks and availability clocks stay separate; unknown clocks remain NULL.

`MarketData.read_tagged(dataset, ...)` supplies source attribution for every
catalogued dataset on both DuckDB and Parquet. Column projection retains its
metadata. Existing `read` and `export` schemas remain compatible. Each new release
manifest records the source column and an explicit fallback for every table.
Existing immutable Parquet files are retained rather than rewritten to repeat
already-present source fields. A legacy row without a known source is labelled
`legacy_unverified:<store>.<table>`; this is a missing-provenance marker, not an
invented provider. A recorded source without a raw receipt link stays explicitly
unlinked. The migration does not establish full historical traceability.

For futures daily and futures-option settlement rows, `read_tagged` also resolves
an explicit producer receipt membership: source, dataset, day and contract ID must
all match the receipt's `native_parameters.contract_ids`. Exactly one checksum-backed
receipt must match; absent or ambiguous memberships remain unlinked. Parquet lookup
uses receipts from the same pinned release. This read-side attribution does not
change stored values, original `read` results, or unknown historical clocks.

`iv_observations_v2` requires source and method in its primary key, alongside
instrument and date. DuckDB retains independent observations; new Parquet facts
partition by datasource and calendar month. `data_source_registry` describes the
producer/method, and `source_receipts` indexes original receipt/raw checksums.
Legacy values are copied unchanged, with missing receipt links and clocks left
unknown. New checksum-backed records can upgrade linkage without changing values.

All IV carries `consistency_tag=cross_source_inconsistent` and
`comparison_status=not_compared`. This is the user's instruction to prevent
assuming cross-source equivalence; it is not evidence that a numerical comparison
was performed or that a particular value is wrong. No cross-vendor comparison,
splicing, averaging or QC equivalence is implied. IBKR underlying 30-day IV,
Theta native contract IV, and any separately documented ATM/tenor aggregation
remain distinct methods. Screener 252 observations and QC 504 observations remain
separate coverage requirements.

Publication requires exact selected-row inclusion and whole-release verification.
A canonical merge or an attribution-capable SDK is not a completed published
migration. Operational proofs under `runtime/var/maintenance/storage-v2-*` record
which stages have actually completed. No re-download is needed for this redesign.

Theta EOD normalization retains each native contract IV and timestamp in its raw
receipt. The separately derived `thetadata_eod_atm45_v1` series uses a common
nearest-ATM call/put strike and the retained listed expiration nearest 45 days
within 30–60 days. It is a mean of vendor IV values, not a new solver result or
IBKR 30-day IV. Missing valid pairs are rejected with evidence. The normalized
underlying clock is the vendor's reported `underlying_timestamp`, converted from
America/New_York to UTC; its market-event semantics and synchronization remain
unverified. No common option quote clock or historical availability is invented.
