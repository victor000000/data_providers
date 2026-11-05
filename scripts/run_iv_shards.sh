#!/bin/bash
# run_iv_shards.sh — download ATM IV for a whole universe, sharded.
#
# WHY SHARDS. One process is latency-bound, not rate-bound: measured 2026-10-03, a single
# download_atm_iv run with 16 workers held the Alpaca gate at 48 req/min of the 190 allowed,
# because each request takes ~20 s and one process cannot keep enough in flight. Eight
# shards reached ~190/min — the gate's ceiling — for the same universe.
#
# WHY A DISTINCT STAGING KEY PER SHARD. DuckDB allows one writer per file. Each run's
# skip/coverage queries read `mh` (its own staging) UNION `mhro` (the market_history
# snapshot), so distinct files are what let 8 processes share a universe without re-fetching
# each other's work. merge_staging.py discovers staging/*.duckdb by itself, so the merge
# needs no per-shard bookkeeping — but it must be run afterwards: see watch_iv_merge.sh.
#
# WHY --gapfill. A band is requested whole, so widening it (ATM±5 -> ±7) would re-request
# the strikes already held. --gapfill reads what we hold and asks only for the rest.
# Verified: AAPL 2024-11-15 held 14 strikes, fetched 16, overlap zero.
#
# STAGGERED LAUNCHES. Eight market_history snapshots landing in the same second is the I/O
# storm that pegged the disk on 2026-10-03, before core/db.py pinned a per-process resource
# profile. A few seconds of offset keeps the snapshots and first checkpoints from aligning.
#
#   bash scripts/run_iv_shards.sh [tickers-file] [n-shards] [workers-per-shard] [extra args...]
#
# Example — widen the main screen universe to ATM±7 without re-downloading ±5:
#   bash scripts/run_iv_shards.sh plans/universe_top1000.txt 8 12 --strikes 7 --gapfill
set -u
cd "$(dirname "$0")/.." || { echo "cannot cd to the repo root"; exit 1; }
CODE_HOME="$PWD"
PYTHON="$CODE_HOME/.venv/bin/python"
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$("$PYTHON" -c 'from data_providers.runtime import ROOT; print(ROOT)')}"
cd "$DATA_PROVIDERS_HOME" || exit 1


TICKERS_FILE="${1:-plans/universe_top1000.txt}"
N="${2:-8}"
WORKERS="${3:-12}"
shift $(( $# < 3 ? $# : 3 ))          # everything left over is passed through
EXTRA=("$@")

[ -f "$TICKERS_FILE" ] || { echo "no such tickers file: $TICKERS_FILE" >&2; exit 1; }

# Round-robin (stride N), so consecutive names land on different shards and one slow
# ticker cannot stall a contiguous block.
OUT="logs/iv_shards"
mkdir -p "$OUT"
rm -f "$OUT"/iv.*.list
awk -v n="$N" -v out="$OUT" '
    /^[[:space:]]*#/ { next }
    NF == 0          { next }
    { print $1 >> (out "/iv." (i % n) ".list"); i++ }
' "$TICKERS_FILE"

echo "=== IV shards start $(date -u '+%F %T') UTC · $(grep -cvE '^\s*(#|$)' "$TICKERS_FILE") names · $N shards · ${WORKERS}w ==="
pids=()
for i in $(seq 0 $((N-1))); do
    f="$OUT/iv.$i.list"
    [ -s "$f" ] || { echo "  shard $i: EMPTY, skipped"; continue; }
    [ "$i" -gt 0 ] && sleep 6
    echo "--- shard $i: $(wc -l < "$f") names -> staging atm_iv$i ---"
    setsid nohup "$PYTHON" -m data_providers.jobs.history.download_atm_iv \
        --tickers-file "$f" --staging "atm_iv$i" --workers "$WORKERS" "${EXTRA[@]+"${EXTRA[@]}"}" \
        > "logs/atm_iv_shard$i.log" 2>&1 < /dev/null &
    pids+=($!)
done

rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
echo "=== IV shards end $(date -u '+%F %T') UTC rc=$rc ==="
exit $rc
