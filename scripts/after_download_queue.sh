#!/bin/bash
# after_dl.sh — drain the queued work once the last downloader exits.
#
# Every step needs a READ-WRITE open on market_history, which DuckDB refuses while any
# writer holds it. So they cannot run alongside the download; they must run after it.
#
# Each step's EXIT CODE is kept. Every call used to be `... | tail -N`, whose status is
# TAIL's, so a merge that duplicated rows (merge_staging exits 1) or kept staging files
# unmerged (exits 2) was indistinguishable from a clean one -- and the only signal left,
# the final banner, printed "queue drained" whatever had happened.
#
# cd relative to THIS script rather than a hardcoded absolute path: an absolute cd that
# fails leaves every relative path below resolving in the wrong tree, silently.
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
      2) echo "  !! $label INCOMPLETE (rc=2) — staging NOT fully merged; files kept on disk" ;;
      *) echo "  !! $label FAILED (rc=$rc) — see the lines above" ;;
    esac
    fail=1
  fi
}

while :; do
  alive=0
  for p in $(ls /proc | grep -E '^[0-9]+$'); do
    c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
    # Match the script's BASENAME, not its full path.
    #
    # The path is not stable: the data/application split moves this file from scripts/ to
    # core/sources/, and a hard-coded "scripts/download_option_bars.py" here would stop
    # matching the moment it moves — the loop would decide the downloader had exited and
    # start merging into market_history WHILE it was still writing. Silent, and exactly the
    # kind of failure this watcher exists to prevent.
    #
    # Basename matching cannot make the watcher match itself: its own cmdline is just
    # "bash scripts/after_download_queue.sh", which does not contain the script name.
    case "$c" in *download_option_bars.py*) alive=1;; esac
  done
  [ $alive -eq 0 ] && break
  sleep 30
done

echo "=== downloader exited $(date -u +%FT%TZ) ==="

echo "--- 0. re-download guard (did the run fetch what we already had?) ---"
run "re-download guard" 25 "$PYTHON" -m data_providers.data.staging.download_guard

# merge_staging.py takes NO --source: it discovers staging/*.duckdb itself (line 83) and
# folds every one of them. The first version of this script looped over the files and
# passed --source "$s", which argparse rejected with "unrecognized arguments" -- so the
# merge silently did nothing and the IV step below ran against un-merged data.
echo "--- 1. merge ALL staging ---"
run "merge staging" 12 "$PYTHON" -m data_providers.data.staging.merge_staging

echo "--- 2. compute IV history from the option bars just fetched ---"
# This is the step that produces the 2-year IV history WITHOUT TWS: download_atm_iv.py
# derives IV from option prices via Black-Scholes (bs.implied_vol), so any name whose
# option bars we hold gets an IV series. The IBKR sweep is an alternative route to the
# same column, not the only one -- which is why the ushmds outage blocks the sweep but
# does not block the objective.
run "compute IV" 25 "$PYTHON" -m data_providers.jobs.history.download_atm_iv --tickers-file plans/high_iv_priority.txt

echo "--- 3. utc repair ---"
run "utc repair" 12 "$PYTHON" -m data_providers.jobs.audit.utc_repair --apply

echo "--- 4. duplicate check ---"
run "duplicate check" 8 "$PYTHON" -m data_providers.jobs.audit.check_duplicates

if [ "$fail" = 0 ]; then
  echo "=== queue drained $(date -u +%FT%TZ) ==="
else
  echo "=== QUEUE DRAINED WITH FAILURES $(date -u +%FT%TZ) — see the !! lines above ==="
fi
exit "$fail"
