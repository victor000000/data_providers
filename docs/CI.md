# Continuous integration

The `Shared data checks` GitHub Actions workflow runs on pushes, pull requests,
and manual dispatch. It uses read-only repository permissions and a bounded
Ubuntu job to run the same native commands used locally:

```bash
make setup
make test
make build
```

Setup installs the locked dependencies into `.venv-native` with a private Python.
Tests redirect `DATA_PROVIDERS_HOME` to a temporary directory through
`tests/conftest.py`. They do not require provider credentials, a broker terminal,
market downloads, or live trading services. Package builds produce a wheel and
source distribution under `dist/`.

These checks cover isolated code behavior and packaging. They do not certify
provider entitlements, live network routes, data freshness, or production delivery.
Those require the separate operational evidence described in `OPERATIONS.md`.

Continuous deployment is not configured: this repository has no declared
deployment target or package-release credentials. The workflow does not deploy,
publish a package, restart a service, or perform market-data maintenance.

The public source snapshot excludes private runtime/configuration and operational
data. Broker account identifiers in trading-log helpers are placeholders and must
be configured for a deployment. Credentials belong in private local storage,
never in this repository or its workflow logs.
