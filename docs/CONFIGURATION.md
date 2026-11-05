# Configuration reference

This guide describes configuration implemented in the source, reviewed on
2026-10-31. It is a reference, not a request to change the live installation.
Use [Native environment](NATIVE.md) for setup and [Operations](OPERATIONS.md)
before running or resuming jobs.

## Choose the runtime explicitly

The current live storage root is `/home/vic/data_providers/runtime`. Code and
storage have separate ownership. Do not create replacement stores in consumer
projects or move this runtime without a user request.

`MarketData(home=...)` and CLI `--home` override the default for that client.
Without an explicit home, `data_providers.runtime.data_home()` resolves:

1. Nonempty `DATA_PROVIDERS_HOME`, expanded and resolved as a path.
2. `[storage].home` in the file named by `DATA_PROVIDERS_CONFIG`, or the checkout's
   `config.local.toml` when that variable is unset. Relative storage paths resolve
   against the configuration file's directory.
3. `runtime/` beside `pyproject.toml` when running from a source checkout.
4. `~/.local/share/data-providers` in an installation without that checkout marker.

A present but malformed configuration raises an error. It is not an instruction
to initialize another store. `runtime.ROOT` is computed at module import; configure
the environment before importing ingestion modules or starting a worker.

The native wrapper changes directory to the project root and supplies
`DATA_PROVIDERS_HOME=$PWD/runtime` unless already set. Consequently, normal wrapper
commands use this environment value ahead of TOML, including a custom TOML path.
A CLI `--home` is a client setting; use `DATA_PROVIDERS_HOME` when all imported job
modules need the same root. The maintenance supervisor propagates its selected
home to subprocesses.

Configuration shape, from [config.example.toml](../config.example.toml):

```toml
[storage]
home = "./runtime"
```

Inspect the effective paths without opening a database or calling a provider:

```bash
./scripts/native.sh python - <<'PYCODE'
from data_providers.runtime import data_home, trading_logs_dir
print("data:", data_home())
print("trading inputs:", trading_logs_dir())
PYCODE
```

## Native command environment

| Setting | Behavior |
| --- | --- |
| `.venv-native/` | Locked host environment selected by `scripts/native.sh` through `PATH` |
| `runtime/python/bin/python3.14` | Existing private interpreter preferred by setup |
| `.python-native/` | Private Python download location used by fresh setup/build |
| `UV_PROJECT_ENVIRONMENT` | Setup fixes this to the project's `.venv-native` |
| `UV_HTTP_TIMEOUT` | Setup default 30 seconds; existing override is preserved |
| `UV_CONCURRENT_DOWNLOADS` | Setup default 2; existing override is preserved |
| `TZ` | Wrapper and runtime module set UTC; legacy naive timestamps mean UTC unless a field explicitly documents a source-local clock |
| `.venv` | Setup creates a compatibility symlink only when no path/link exists; it preserves an existing different environment |

`make setup` syncs the committed lock with source, analysis and development extras.
The package supports Python >=3.11; this host's setup selects private Python 3.14.
These are distinct compatibility and deployment statements. Use `make setup`
rather than an unqualified `uv sync`. Consumers retain their own environments.

## Trading input selection

`TRADING_LOGS_DIR` is an external source directory, independent of the runtime.
The native wrapper defaults it to
`/mnt/c/Users/victo/Documents/trading_logs`. A nonempty explicit override wins.

Without the wrapper, `trading_logs_dir()` checks an explicit override, then an
existing `/root/trading_logs`, then the existing Windows mount above, finally
`~/trading_logs`. Those compatibility fallbacks do not prove that an authoritative
source is mounted or fresh. Normal host maintenance must use the current mounted
exports and the canonical scan described in [Maintenance](MAINTENANCE.md).
A missing source blocks trading loaders and broker refreshes.

## Network and reader settings

| Variable | Meaning and limits |
| --- | --- |
| `IB_HOST` | Wrapper default `127.0.0.1`; override for the actual broker host |
| `IB_PORT` | Shared TWS adapter default `4001`; individual broker jobs may have their own candidate-port behavior |
| `FUTU_HOST` | Wrapper default `127.0.0.1` |
| `FUTU_PORT` | Adapter setting; inspect the selected adapter rather than assuming an IB port |
| `THETADATA_BASE_URL` | Theta endpoint; default `http://127.0.0.1:25503/v3` |
| `THETADATA_API_KEY` | Optional Theta credential input; never print it in diagnostics |
| `MASSIVE_API_KEY` | Massive credential input; existing private configuration may also supply it |
| `THETA_HISTORY_SLOTS` | Default two slots; explicit four-slot mode has operational preconditions in Native environment |
| `DATA_PROVIDERS_TOKEN` | HTTP bearer token; required for non-loopback listeners |
| `DATA_PROVIDERS_BACKEND` | Legacy connection diagnostic override; the SDK/CLI have their own explicit `backend`/`--backend` argument |

This table covers shared settings, not every legacy job's parameters or credential
fallback. Provider-specific configuration lives with the adapter and private
runtime tools. Consult source before changing it; do not copy credentials into
docs, commands, reports or source control. A successful socket/API probe does not
establish quote freshness, historical access or entitlement.

The standalone data API and the consumer Options Workspace both document port
8765. They are different server processes. If the consumer is already listening,
select another free port for `data-providers serve`, for example `--port 8766`.

## Implementation references

- [Runtime resolution](../data_providers/runtime.py)
- [Native wrapper](../scripts/native.sh)
- [Locked setup](../scripts/setup-native.sh)
- [CLI argument handling](../data_providers/cli.py)
- [Read-only client](../data_providers/client.py)
