#!/bin/bash
# ensure_thetadata.sh — start the local ThetaData terminal if it is not already serving.
#
# WHY THIS EXISTS. The terminal is a long-running Java process bound to 127.0.0.1:25503. It is
# not a network service: nothing restarts it, and when the process dies — torn down with a
# session, killed by an OOM, or simply exited — every ThetaData call starts failing with
# "connection refused" while the rest of the pipeline looks healthy. That reads like the
# vendor being down when in fact the only fault is a local process nobody restarted.
#
# IDEMPOTENT BY DESIGN: if the port already answers, this does nothing and says so. Safe to
# call from a cron/loop on every tick — that is exactly how it is meant to be used.
#
# Exit status: 0 when the terminal is serving (already up, or started and became ready),
# 1 when it could not be brought up. Never starts a SECOND terminal: the port check is the
# lock, so two callers cannot race into two JVMs fighting over one port.
#
# This script performs NO file deletion.

cd "$(dirname "$0")/.." || exit 1
CODE_HOME="$PWD"
export DATA_PROVIDERS_HOME="${DATA_PROVIDERS_HOME:-$("$CODE_HOME/.venv/bin/python" -c 'from data_providers.runtime import ROOT; print(ROOT)')}"
PYTHON="$CODE_HOME/.venv/bin/python"
cd "$DATA_PROVIDERS_HOME" || exit 1

PORT=25503
LOG=logs/thetadata_terminal.log

# A configured Windows/remote terminal is a valid source; do not launch a second local JVM.
"$PYTHON" - <<'PY_REMOTE'
import sys
from urllib.parse import urlsplit
from data_providers.providers.thetadata import get_client
client=get_client()
# Readiness must never issue market history or replay an entitlement refusal.
try:
    response = client.session.get(client.base_url + '/terminal/mdds/status', timeout=(2, 5))
    if response.status_code == 200 and response.text.strip().strip('\"').upper() == 'CONNECTED':
        sys.exit(0)
except Exception:
    pass
endpoint=urlsplit(client.base_url)
sys.exit(1 if endpoint.hostname in ('127.0.0.1','localhost','::1') else 2)
PY_REMOTE
remote_rc=$?
if [ "$remote_rc" -eq 0 ]; then
  echo "thetadata: configured endpoint is serving"; exit 0
elif [ "$remote_rc" -eq 2 ]; then
  echo "thetadata: configured remote endpoint unavailable; check its terminal/upstream"; exit 1
fi

up() { timeout 3 bash -c "echo > /dev/tcp/127.0.0.1/$PORT" 2>/dev/null; }

if up; then
  echo "thetadata: already serving on $PORT"
  exit 0
fi

if pgrep -f ThetaTerminal >/dev/null 2>&1; then
  # Process alive but the port is closed: still booting, or wedged. Give it time before
  # declaring failure — a cold boot takes ~2-3 minutes to bind.
  echo "thetadata: process running, waiting for port $PORT to bind (up to 240s)..."
  for _ in $(seq 1 48); do
    up && { echo "thetadata: ready"; exit 0; }
    sleep 5
  done
  echo "thetadata: process alive but port $PORT never opened — leaving it alone (not killing"
  echo "           a process that may be mid-boot; check $LOG)"
  exit 1
fi

JAVA_BIN=$(PATH="$DATA_PROVIDERS_HOME/tools/java21/current/bin:${JAVA_HOME:-/opt/java/openjdk}/bin:$HOME/.local/java/current/bin:/opt/java/current/bin:$PATH" command -v java)
if [ -z "$JAVA_BIN" ]; then
  echo "thetadata: Java 21+ runtime missing; terminal cannot start"
  exit 1
fi

# The bootstrap launches the versioned terminal using plain java.
export PATH="$(dirname "$JAVA_BIN"):$PATH"

KEY=$("$CODE_HOME/.venv/bin/python" -c "import json;print(json.load(open('secrets/thetadata.json'))['api_key'])" 2>/dev/null)
if [ -z "$KEY" ]; then echo "thetadata: no api_key in secrets/thetadata.json"; exit 1; fi

echo "thetadata: not running — starting the terminal (key loaded, ${#KEY} chars)"
export JAVA_TOOL_OPTIONS="-DsocksProxyHost=127.0.0.1 -DsocksProxyPort=7897 -Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=7897 -Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=7897"
export GRPC_PROXY_EXP=127.0.0.1:7897
mkdir -p logs
cd tools || exit 1
setsid nohup "$JAVA_BIN" -jar ThetaTerminalv3.jar --api-key "$KEY" \
  >> "../$LOG" 2>&1 < /dev/null &

for _ in $(seq 1 48); do
  up && { echo "thetadata: started and serving on $PORT"; exit 0; }
  sleep 5
done
echo "thetadata: started but port $PORT did not open within 240s — see $LOG"
exit 1
