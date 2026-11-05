#!/bin/bash
set -eu
cd "$(dirname "$0")/.."
exec .venv/bin/python -m data_providers.cli maintain --execute "$@"
