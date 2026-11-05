#!/bin/bash
# full_pipeline.sh — deliver the IV history, then complete the full universe sweep.
#
# ORDER MATTERS, and it is set by DuckDB's single-writer rule plus the snapshot problem:
#
#   * The sweep must run when market_history is FREE. If a writer holds it, db.connect()
#     cannot snapshot, `mhro` degrades to empty views over staging, and the sweep re-fetches
#     the entire universe -- the 753,014-row waste already measured once. That is why the
#     sweep is LAST here and not concurrent with the priority job.
#   * The IV computation must run before the sweep, so the bars it needs are merged and the
#     delivery is not delayed behind a multi-hour sweep.
#
# So: wait for the priority fetch -> merge -> compute IV -> repair -> verify -> THEN sweep.
#
# EXIT CODES ARE THE POINT. merge_staging.py reports 0 clean / 1 duplicates found / 2 rows
# left unmerged (it keeps the staging file rather than destroying it), and every call here
# used to be `... | tail -N`, whose status is TAIL's. A merge that duplicated rows or kept
# files unmerged therefore looked exactly like a clean one, and the only signal an operator
# had -- the banner on the last line -- printed "pipeline complete" unconditionally. Each
# step now reports its own status and the banner tells the truth.

# cd relative to THIS script, and fail loudly. The hardcoded `cd /root/dte_21_screener`
# would silently leave the shell in the wrong directory -- every relative path below
# (plans/, scripts/, the DuckDB files) then resolves elsewhere, and the pipeline runs
# against the wrong tree with no error at all.
cd "$(dirname "$0")/.." || { echo "cannot cd to the repo root"; exit 1; }
CODE_HOME="$PWD"
PYTHON="$CODE_HOME/.venv/bin/python"
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$("$PYTHON" -c 'from data_providers.runtime import ROOT; print(ROOT)')}"
cd "$DATA_PROVIDERS_HOME" || exit 1


fail=0
run() {   # run <label> <tail-lines> <cmd...>
  local label="$1" n="$2"; shift 2
  "$@" 2>&1 | tail -"$n"
  local rc=${PIPESTATUS[0]}
  if [ "$rc" != 0 ]; then
    case "$rc" in
      2) echo "  !! $label INCOMPLETE (rc=2) — staging NOT fully merged; "
         echo "     the files are still on disk, re-run after fixing" ;;
      *) echo "  !! $label FAILED (rc=$rc) — see the lines above" ;;
    esac
    fail=1
  fi
}

while :; do
  alive=0
  for p in $(ls /proc | grep -E '^[0-9]+$'); do
    c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
    # Match the SCRIPT NAME, not a path. The old pattern was the literal string
    # "scripts/download_option_bars.py", so a job launched as an absolute path, from
    # another directory, or through the shim at a different depth did not match -- and
    # the wait would fall through immediately, starting the sweep while the priority
    # fetch still held the writer lock. That is the exact condition this loop exists to
    # prevent, so a miss here is not cosmetic.
    case "$c" in *"download_option_bars.py"*) alive=1;; esac
  done
  [ $alive -eq 0 ] && break
  sleep 30
done
echo "=== priority fetch exited $(date -u +%FT%TZ) ==="

echo "--- 1. merge ALL staging ---"
run "merge staging" 12 "$PYTHON" -m data_providers.data.staging.merge_staging

echo "--- 2. compute IV history (the delivery) ---"
run "compute IV" 8 "$PYTHON" -m data_providers.jobs.history.download_atm_iv \
    --tickers-file plans/high_iv_priority.txt

echo "--- 3. merge the IV rows ---"
run "merge IV rows" 8 "$PYTHON" -m data_providers.data.staging.merge_staging

echo "--- 4. duplicate check ---"
run "duplicate check" 6 "$PYTHON" -m data_providers.jobs.audit.check_duplicates

echo "--- 5. FULL UNIVERSE SWEEP (market_history is free, so the snapshot will work) ---"
run "universe sweep" 10 "$PYTHON" -m data_providers.jobs.history.download_option_bars \
    --tickers-file plans/universe_download.txt --bulk

echo "--- 6. IV for the whole swept universe ---"
run "final merge" 6 "$PYTHON" -m data_providers.data.staging.merge_staging
run "final IV" 6 "$PYTHON" -m data_providers.jobs.history.download_atm_iv

if [ "$fail" = 0 ]; then
  echo "=== pipeline complete $(date -u +%FT%TZ) ==="
else
  echo "=== PIPELINE FINISHED WITH FAILURES $(date -u +%FT%TZ) — see the !! lines above ==="
fi
# Propagate. Without this the script's own status was always 0, so a supervisor
# chaining on it could not tell a clean run from one where every step failed.
exit "$fail"
