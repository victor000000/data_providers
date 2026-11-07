# Daily readiness assessment

`MarketData.readiness(contract)` assesses exact requested dates against one pinned
release (or explicitly provisional live stores). It is read-only. Example:

```json
{
  "version": 1,
  "dataset": "us_daily",
  "symbols": ["SPY"],
  "sessions": ["2026-11-02", "2026-11-03", "2026-11-04"],
  "filters": {"source": "YOUR_EXACT_STORED_SOURCE"},
  "required_columns": ["close"]
}
```

The filters object is mandatory and must include source or datasource_tag.
Only required_columns may be omitted; then no extra field-quality checks apply.
Supply the actual stored source value and session dates from your exchange calendar.
Optional additional equality filters can isolate method, model or venue columns.
Symbols use the dataset's catalog identity; CN names must retain venue qualification.

```bash
./scripts/native.sh data-providers --release RELEASE_ID readiness contract.json
```

The SDK accepts the same JSON object as a Python dictionary. Output includes the
normalized contract and its SHA-256, backend and release, assessment time, per-symbol
present and missing dates and qualifying row counts. Contract lists are normalized
so ordering does not change the hash. Dates must be unique YYYY-MM-DD values.
Limits are 20,000 symbols, 10,000 dates, one million symbol/date pairs, 100 filters
and 100 required columns. Invalid input or unavailable datasets cause visible errors.

A covered session has at least one row matching the exact source/other filters,
with non-null required columns and finite required numeric fields. Duplicates do
not increase session coverage. Timestamp dates are evaluated in UTC, which may
differ from an exchange trading-session label; use a dataset with an explicit
session-date column when that distinction matters.

`ready=true` means daily presence in a published release only. It does not certify
full intraday grids, NBBO, positive or otherwise economically valid values,
source freshness, method suitability, whole-release integrity, selected-stage
inclusion or consumer acceptance. Those proofs remain separate. The response
explicitly records `qualification=daily_presence`, `release_integrity=not_assessed`
and `consumer_acceptance=not_assessed`. Live-store results always have
`status=provisional` and `ready=false`, even when daily presence is complete.

For SDK callers, keep the qualification and evidence states together:

```python
from data_providers import MarketData

report = MarketData(release="RELEASE_ID").readiness(contract)
# An example of complete daily presence (not a delivery-acceptance decision):
# report["ready"] == True
# report["coverage_complete"] == True
# report["qualification"] == "daily_presence"
# report["release_integrity"] == "not_assessed"
# report["consumer_acceptance"] == "not_assessed"
```

The v1 ready field is deliberately retained as a convenience alias for
coverage_complete AND a pinned published release. Consumers must evaluate their
separate integrity, inclusion, freshness and acceptance requirements before use.

This command writes no assessment files, changes no runtime state and schedules
no requests. Redirect output to a caller-selected file if a retained handoff is needed.
See [v0.2 design](V02_DESIGN.md) for subsequent migration stages.
