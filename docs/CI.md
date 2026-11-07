# Continuous integration

[Shared data checks](../.github/workflows/check.yml) runs on push, pull request and
manual dispatch in a bounded Ubuntu job with read-only repository permissions.
Its clean-checkout sequence is:

```bash
make setup
make docs-check
./scripts/native.sh python -m ruff check --no-cache data_providers/readiness.py tests/test_readiness.py scripts/check_docs.py tests/test_docs_check.py
make test
make build
```

Setup uses the committed lock, all source/analysis/dev extras, `.venv-native` and
private Python 3.14. The metadata minimum >=3.11 is not an additional CI matrix.
Tests select an isolated temporary runtime in tests/conftest.py. They require no
provider credentials, broker terminal, market downloads or live trading services.
Documentation checks parse every shared CLI command's help with an absent temporary
runtime, compare catalog/version contracts and check portable canonical links.

The workflow supplies PYTEST_ADDOPTS=--junitxml=test-results/unit.xml for the full
suite. An always-running upload step retains that XML and any built dist/*.whl
and dist/*.tar.gz for 14 days. Failed setup may leave no results; an absent artifact
is not a passing test run. Do not upload runtime stores, private config, credentials,
raw receipts or account data. Documentation checker scope and exclusions are in
[the audit](DOC_REVIEW_20261107.md#checks-and-limits).

Continuous deployment/package publication is not configured. The workflow does
not restart services, submit orders, perform acquisition or publish a data-lake
release. CI success establishes tested code and packaging only; see
[Validation](VALIDATION.md) for evidence boundaries and [Operations](OPERATIONS.md)
for independently authorized recovery.

Acceptance must match the final source SHA and a terminal hosted run. Local commands,
an older baseline run or an unpushed commit cannot establish that result. Serialized
publication belongs to the existing coordinator/publisher; see
[Development](DEVELOPMENT.md#ci-and-release-review) and [Evidence access](EVIDENCE.md).
