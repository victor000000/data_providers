#!/bin/bash
# monitor_downloads.sh — watch every downloader and speak only when something is wrong.
#
# Run this alongside any download (foreground, or under tmux/nohup for a long job). It tails
# the day's logs and emits one line per problem — nothing else — so its output IS the list of
# things worth acting on.
#
#   bash scripts/monitor_downloads.sh              # follow all current logs
#   bash scripts/monitor_downloads.sh --summary    # one-shot health check, exit non-zero if bad
#
# Why a dedicated monitor: every bug found on 2026-10-01 announced itself in a log line and
# was missed because the line looked like success. A 403 "OPRA agreement is not signed" showed
# up as "0 rows"; a dropped TWS gateway link showed up as "no bars". The filter below matches
# FAILURE SIGNATURES, not the absence of success — a monitor that only watches for progress
# stays silent through a crashloop, and silence reads exactly like "still working".
set -u
cd "$(dirname "$0")/.." || { echo "cannot cd to the repo root"; exit 1; }
CODE_HOME="$PWD"
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$("$CODE_HOME/.venv/bin/python" -c 'from data_providers.runtime import ROOT; print(ROOT)')}"
PYTHON="$CODE_HOME/.venv/bin/python"
cd "$DATA_PROVIDERS_HOME" || exit 1

D=$(date -u +%Y%m%d)

PATTERN='batch fail|Traceback|consecutive failures|OPRA|invalid symbol|still empty|Error 1100|Error 1102|failed after|no bars|Timeout for'
LOGS=$(ls logs/*_$D.log 2>/dev/null; find var/maintenance -type f -path "*/${D}*/*.log" 2>/dev/null)

if [ "${1:-}" = "--summary" ]; then
    bad=0
    echo "=== download health $(date -u '+%F %T') UTC ==="
    for f in $LOGS; do
        n=$(grep -acE "$PATTERN" "$f" 2>/dev/null || true); n=${n:-0}
        ok=$(grep -acE ': \+[0-9]+ (rows|bars|1-min)' "$f" 2>/dev/null || true); ok=${ok:-0}
        age=$(( $(date -u +%s) - $(date -u -r "$f" +%s) ))
        printf '  %-30s ok=%-7s problems=%-5s log %ss old\n' "$(basename "$f")" "$ok" "$n" "$age"
        [ "$age" -gt 900 ] && { echo "      ^ STALE — writer may have died"; bad=1; }
        [ "$n" -gt 0 ] && bad=1
    done
    echo
    echo "=== running ==="
    for p in /proc/[0-9]*; do
        c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)
        case "$c" in *data_providers.jobs.*|*scripts/download*|*scripts/fetch*) echo "  $(basename "$p") $(echo "$c" | grep -oE 'data_providers\.[a-z_.]+|scripts/[a-z_]+\.py' | head -1)";; esac
    done | sort -u -k2
    exit $bad
fi

[ -z "$LOGS" ] && { echo "no logs for $D"; exit 1; }
echo "watching: $(echo $LOGS | tr '\n' ' ')"
echo "--- one line per problem; Ctrl-C to stop ---"
tail -F -n 0 $LOGS 2>/dev/null | grep --line-buffered -E "$PATTERN"
