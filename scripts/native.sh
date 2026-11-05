#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$PWD/runtime}"
export TRADING_LOGS_DIR="${TRADING_LOGS_DIR:-/mnt/c/Users/victo/Documents/trading_logs}"
export IB_HOST="${IB_HOST:-127.0.0.1}"
export FUTU_HOST="${FUTU_HOST:-127.0.0.1}"
export TZ=UTC
export PATH="$PWD/.venv-native/bin:$PATH"
exec "$@"
