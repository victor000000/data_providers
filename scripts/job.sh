#!/bin/bash
# job.sh — start a long download once, and never a second copy of it.
#
# WHY THIS EXISTS. Every download here is expensive to run twice and cheap to check. Two
# `download_atm_iv` processes would split the Alpaca gate between them so both finish
# later; two CN jobs would queue on the one-writer-per-file lock and look hung. A bare
# `nohup ... &` has no memory of itself, so a supervisor that wakes up an hour later
# cannot tell "already running" from "died at 03:00" — and on 2026-10-11 that is exactly
# the question the hourly loop has to answer.
#
# THE PID FILE IS THE LOCK, and the recorded pid is a process GROUP leader (setsid), so
# stopping a job stops its children too. That matters: on 2026-10-10 a killed supervisor
# left two shards at PPID 1 fetching a dead gateway for six hours.
#
#   scripts/job.sh <name> <cmd...>     start unless <name> is already running
#   scripts/job.sh --status            one line per known job (RUNNING/DONE, age, log)
#   scripts/job.sh --stop <name>       SIGTERM the group, then SIGKILL
#
# Exit: 0 started, already running, or completed successfully · 3 failed within 20s
set -u
cd "$(dirname "$0")/.." || exit 1
CODE_HOME="$PWD"
if [ -n "${DATA_PROVIDERS_HOME:-}" ]; then DATA_HOME="$DATA_PROVIDERS_HOME"
elif [ -x .venv/bin/python ]; then DATA_HOME=$(.venv/bin/python -c 'from data_providers.runtime import ROOT; print(ROOT)')
else DATA_HOME="$PWD"; fi
export DATA_PROVIDERS_HOME="$DATA_HOME"
RUN="$DATA_HOME/var/run"; LOG="$DATA_HOME/logs"; mkdir -p "$RUN" "$LOG"

process_identity() {
  local stat boot; local -a fields
  stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
  boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) || return 1
  read -r -a fields <<< "${stat##*) }"
  [ "${#fields[@]}" -ge 20 ] || return 1
  printf '%s:%s\n' "$boot" "${fields[19]}"
}

alive() {   # $1 = name → the pid, if that job's process is still the one we recorded
  local f="$RUN/$1.pid" pid want got birth current
  [ -f "$f" ] || return 1
  pid=$(head -1 "$f" 2>/dev/null) || return 1
  [ -n "$pid" ] && [ -d "/proc/$pid" ] || return 1
  got=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null); got=${got% }
  [ -n "$got" ] || return 1  # A zombie exists in /proc but cannot do work.
  birth=$(sed -n 3p "$f")
  if [ -n "$birth" ]; then
    current=$(process_identity "$pid") || return 1
    [ "$birth" = "$current" ] || return 1
    echo "$pid"; return 0  # An exec changes argv, but preserves process birth identity.
  fi
  # Legacy two-line markers retain their command comparison until the next launch.
  want=$(sed -n 2p "$f")
  # Strict equality is not enough: bash exec-optimizes `bash -c "cd X; cmd"` into the bare
  # `cmd` process, dropping the recorded prefix. On 2026-10-16 the live ibkr_sweep read as
  # STOPPED (one --status away from a duplicate launch) while both its shards fetched. The
  # LAST '; '-separated command of the recorded line is still the same identity.
  if [ "$want" != "$got" ]; then
    # Strip env before looking for a shell's exec-optimized final command: an env-wrapped
    # bash -c may still be running with semicolons inside its original argument.
    for _ in 1 2; do
      if [[ "$want" == env\ * ]]; then
        want="${want#env }"
        while [[ "$want" =~ ^[a-zA-Z_][a-zA-Z0-9_]*= ]]; do
          [[ "$want" == *" "* ]] || return 1
          want="${want#* }"
        done
      fi
      [ "$want" = "$got" ] && break
      want="${want##*; }"
    done
    [ "$want" != "$got" ] && return 1
  fi
  echo "$pid"
}

status() {
  for f in "$RUN"/*.pid; do
    [ -e "$f" ] || continue
    local n; n=$(basename "$f" .pid)
    local p; p=$(alive "$n")
    if [ -n "${p:-}" ]; then printf '%-16s RUNNING pid=%s up=%s\n' "$n" "$p" "$(ps -o etime= -p "$p" | tr -d ' ')"
    else printf '%-16s STOPPED  (log %s)\n' "$n" "$(ls -t "$LOG/${n}"_*.log 2>/dev/null | head -1)"; fi
    # A pre-job.sh `nohup` log named <name>.out is a TRAP for the reader, not the starter:
    # it stops updating while still looking like the live log. On 2026-10-11 that made a
    # running sweep read as idle at 2/min, because `ibkr_sweep.out` is the obvious name to
    # tail and the live one is `ibkr_sweep_<date>.log`. Say so rather than let it mislead.
    [ -e "$LOG/$n.out" ] && printf '%-16s   ^ but %s is a STALE pre-job.sh log — do not tail it\n' "" "$LOG/$n.out"
  done
}

case "${1:-}" in
  --status) status; exit 0 ;;
  --stop)
    n="${2:?usage: job.sh --stop <name>}"; p=$(alive "$n") || { echo "$n is not running"; exit 0; }
    kill -TERM -"$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null
    for _ in {1..40}; do sleep 1; alive "$n" >/dev/null || break; done
    alive "$n" >/dev/null && kill -KILL -"$p" 2>/dev/null
    rm -f "$RUN/$n.pid"; echo "stopped $n"; exit 0 ;;
  ""|-*) echo "usage: job.sh <name> <cmd...> | --status | --stop <name>"; exit 2 ;;
esac

name="$1"; shift
# A bare word that is not a flag is a mistyped subcommand far more often than a job name:
# `job.sh status` used to start a job CALLED "status", with no command, leaving a pid file and
# a log whose whole content was `setsid: no command specified`.
[ $# -gt 0 ] || { echo "usage: job.sh <name> <cmd...> | --status | --stop <name>"; exit 2; }
if p=$(alive "$name"); then
  echo "$name already running (pid $p) — not starting a second copy"; exit 0
fi
log="$LOG/${name}_$(date +%Y%m%d).log"
echo "=== $name start $(date -u +%FT%TZ) :: $* ===" >> "$log"
setsid "$@" >> "$log" 2>&1 < /dev/null &
pid=$!
printf '%s\n%s\n%s\n' "$pid" "$*" "$(process_identity "$pid")" > "$RUN/$name.pid"
sleep 20
if ! kill -0 "$pid" 2>/dev/null; then
  wait "$pid"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "$name completed successfully within 20s — see $log"; exit 0
  fi
  echo "$name failed (rc=$rc) within 20s — see $log"; tail -15 "$log"; exit 3
fi
echo "$name started pid=$pid log=$log"
