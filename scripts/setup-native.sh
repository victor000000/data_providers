#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_PROJECT_ENVIRONMENT="$PWD/.venv-native"
export UV_PYTHON_INSTALL_DIR="$PWD/.python-native"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-30}"
export UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-2}"
native_python=3.14
if [[ -x "$PWD/runtime/python/bin/python3.14" ]]; then
    native_python="$PWD/runtime/python/bin/python3.14"
fi
uv sync --locked --python "$native_python" --extra sources --extra analysis --extra dev
if [[ ! -e .venv && ! -L .venv ]]; then
    ln -s .venv-native .venv
elif [[ ! -L .venv || "$(readlink .venv)" != .venv-native ]]; then
    echo 'Native environment ready. Existing .venv preserved; inspect live workers before switching it.'
fi
