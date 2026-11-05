#!/usr/bin/env bash
# watch.sh — one glance at every running job: what it is, whether it is MOVING, what it last did.
#
#   scripts/watch.sh              one report
#   scripts/watch.sh --loop 30    refresh every 30s until Ctrl-C
#
# Progress is judged by two independent signals, because either alone lies:
#   1. CPU growth over a 3-second sample (a worker waiting on the network drops to ~0)
#   2. the age of its var/status/<name>.json (a job that writes status is alive even while idle)
# A job with no CPU growth AND a status older than STALE_SECS is flagged STUCK.

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || { echo "cannot cd to the repo root"; exit 1; }
PYTHON="$ROOT/.venv/bin/python"
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$("$PYTHON" -c 'from data_providers.runtime import ROOT; print(ROOT)')}"
cd "$DATA_PROVIDERS_HOME" || exit 1
STALE_SECS=180
LOOP=0; INTERVAL=30
[ "${1:-}" = "--loop" ] && { LOOP=1; INTERVAL="${2:-30}"; }

tick() {                       # total cpu ticks (utime+stime) for a pid
  awk '{print $14+$15}' "/proc/$1/stat" 2>/dev/null || echo 0
}

report() {
  echo "════════════════════════════════════════════════════════════════════════"
  date -u '+  %Y-%m-%d %H:%M:%S UTC   (%A)'
  echo "════════════════════════════════════════════════════════════════════════"

  # ---- discover workers: the python process, not its bash/uv wrappers
  declare -A PID SCRIPT CPU0
  for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$cmd" in
      # A real worker, at ANY path. This file is the repo's "what is running?" view, so it
      # must not depend on where a script lives: the data/application split moves files out
      # of scripts/, and an anchored 'scripts/*.py' pattern would make every moved worker
      # invisible here — the monitor would report an idle box while jobs were running.
      */bin/python*.py*)
        script=$(echo "$cmd" | grep -oE '[a-z0-9_]+\.py' | head -1)
        [ -z "$script" ] && continue
        # keep the worker with the most CPU per script (wrappers have ~none)
        cur=$(tick "$pid"); key="$script"
        if [ -z "${SCRIPT[$key]:-}" ] || [ "$cur" -gt "${CPU0[$key]:-0}" ]; then
          SCRIPT[$key]=1; PID[$key]=$pid; CPU0[$key]=$cur
        fi
        ;;
    esac
  done

  if [ ${#PID[@]} -eq 0 ]; then
    echo "  no jobs running"
  else
    # second sample, 3 s later — CPU growth is the liveness signal
    sleep 3
    printf "  %-34s %7s %9s %s\n" "JOB" "PID" "CPU/s" "STATUS FILE"
    for key in "${!PID[@]}"; do
      pid=${PID[$key]}; script=$(basename "${key%.py}")
      d=$(( $(tick "$pid") - ${CPU0[$key]} ))
      sf="var/status/${script}.json"
      if [ -f "$sf" ]; then
        age=$(( $(date +%s) - $(stat -c %Y "$sf") ))
        last=$(grep -oE '"(ticker|contract|symbol)": *"[^"]*"|"bars_total": *[0-9]+' "$sf" 2>/dev/null | tr '\n' ' ' | tr -d '"')
        [ "$age" -gt "$STALE_SECS" ] && state="STALE ${age}s" || state="${age}s ago"
        printf "  %-34s %7s %9s %s | %s\n" "$script" "$pid" "$d" "$state" "$last"
      else
        printf "  %-34s %7s %9s %s\n" "$script" "$pid" "$d" "(none yet)"
      fi
    done
  fi

  # ---- which database files can actually be WRITTEN
  #
  # Beware the easy probe: `read_only=True` succeeds whenever no writer holds the file, so it
  # reports "free" for a database that a staged job has attached read-only as `mhro` — and a
  # read-write open is exactly what a merge needs. That false "free" sent a merge into a
  # failure it had no reason to attempt. Probe read-WRITE, then fall back to read-only to
  # tell "nobody has it" apart from "readable, but not by a writer".
  echo "  ── databases (can a writer open it?) ──"
  "$PYTHON" - <<'PY' 2>/dev/null | sed 's/^/  /'
import duckdb, pathlib, re
for f in ("trades", "market_history", "reference", "market_data/yahoo"):
    p = pathlib.Path(f + ".duckdb")
    name = f + ".duckdb"
    if not p.is_file():
        print(f"{name:26} absent (not created by this probe)")
        continue
    try:
        duckdb.connect(str(p)).close()
        print(f"{name:26} free  (mergeable)")
        continue
    except Exception as e:
        rw = repr(e)
    holder = re.search(r"PID (\d+)", rw)
    who = f"held by PID {holder.group(1)}" if holder else "held by another process"
    try:
        duckdb.connect(str(p), read_only=True).close()
        print(f"{name:26} READ-ONLY ONLY — {who}; a merge must wait (readers block writers)")
    except Exception:
        print(f"{name:26} LOCKED — {who}")
PY

  # ---- the queue: jobs waiting on a lock (their bash is alive but holds no DB)
  waiters=$(for p in /proc/[0-9]*; do tr '\0' ' ' < "$p/cmdline" 2>/dev/null; done | grep -c "until uv run" || true)
  [ "${waiters:-0}" -gt 0 ] && echo "  ── $waiters chain(s) waiting for a lock (normal: they start when it frees) ──"

  # ---- orphans: writers this session killed whose python survived (the recurring trap)
  for p in /proc/[0-9]*; do
    pid=${p#/proc/}; ppid=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)
    cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)
    case "$cmd" in */bin/python*scripts/*.py*)
      [ "${ppid:-1}" = "1" ] && echo "  ⚠ ORPHAN pid $pid ($(echo "$cmd" | grep -oE 'scripts/[a-z0-9_]+\.py')) — reparented to init; kill -9 it"
      ;;
    esac
  done
  echo
}

if [ "$LOOP" = "1" ]; then
  while true; do report; sleep "$INTERVAL"; done
else
  report
fi
