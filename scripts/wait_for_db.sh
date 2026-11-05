#!/usr/bin/env bash
# wait_for_db.sh <db.duckdb> [max_minutes] — bounded lock wait.
#
# Every chain today used `until connect; do sleep 30; done`, which waits FOREVER if a writer
# hangs. That is not hypothetical: an orphaned CN backfill held the lock while three chains
# queued silently behind it. This gives up loudly instead, so a stuck writer is visible.
set -u
DB="${1:?usage: wait_for_db.sh market_history.duckdb [max_minutes]}"
MAX="${2:-30}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || { echo "cannot cd to the repo root"; exit 1; }
CODE_HOME="$PWD"
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$("$CODE_HOME/.venv/bin/python" -c 'from data_providers.runtime import ROOT; print(ROOT)')}"
PYTHON="$CODE_HOME/.venv/bin/python"
cd "$DATA_PROVIDERS_HOME" || exit 1

start=$(date +%s)
until "$PYTHON" -c 'import duckdb,pathlib,sys; p=pathlib.Path(sys.argv[1]); assert p.is_file(); duckdb.connect(str(p)).close()' "$DB" 2>/dev/null; do
  waited=$(( ($(date +%s) - start) / 60 ))
  if [ "$waited" -ge "$MAX" ]; then
    echo "GAVE UP after ${MAX}m waiting for $DB — a writer is stuck. Check: scripts/watch.sh" >&2
    exit 1
  fi
  sleep 30
done
echo "$DB is free (waited ${waited:-0}m)"
