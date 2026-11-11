#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SANDBOX="${SANDBOX:-opencode-server-2}"
WORKSPACE="${WORKSPACE:-default}"
LOCAL_OPENCODE_PORT="${LOCAL_OPENCODE_PORT:-4097}"
REMOTE_DIR="${REMOTE_DIR:-/sandbox/workspace}"
NS="${NS:-opencode-internal}"
GATEWAY="${GATEWAY:-openshell-opencode-017}"
RUNTIME_JSON="${RUNTIME_JSON:-$REPO_DIR/deployments/cluster-9blcx/runtime.json}"

ACTION="${1:-attach}"
case "$ACTION" in
  attach|--check|--smoke) ;;
  *) printf 'Usage: %s [--check|--smoke]\n' "$0" >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { printf 'Usage: %s [--check|--smoke]\n' "$0" >&2; exit 2; }
[[ "$LOCAL_OPENCODE_PORT" =~ ^[0-9]+$ ]] \
  && (( LOCAL_OPENCODE_PORT > 0 && LOCAL_OPENCODE_PORT < 65536 )) \
  || { printf 'ERROR: invalid LOCAL_OPENCODE_PORT.\n' >&2; exit 1; }

if [[ -z "${OPENSHELL_CONFIG_HOME:-}" ]]; then
  OPENSHELL_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
fi
OPENCODE_AUTH_FILE="${OPENCODE_AUTH_FILE:-$OPENSHELL_CONFIG_HOME/opencode-remote/auth.env}"
[[ -r "$OPENCODE_AUTH_FILE" ]] || {
  printf 'ERROR: protected OpenCode login file not found: %s\n' "$OPENCODE_AUTH_FILE" >&2
  exit 1
}

export OPENSHELL_CONFIG_HOME OPENCODE_AUTH_FILE NS GATEWAY WORKSPACE SANDBOX LOCAL_OPENCODE_PORT
export OPENSHELL_COLOR=never
"$SCRIPT_DIR/start-devspaces-forwards.sh"

set -a
# This protected local file is created through the approved server-login workflow.
. "$OPENCODE_AUTH_FILE"
set +a
[[ -n "${OPENCODE_SERVER_USERNAME:-}" && -n "${OPENCODE_SERVER_PASSWORD:-}" ]] || {
  printf 'ERROR: login file must define OPENCODE_SERVER_USERNAME and OPENCODE_SERVER_PASSWORD.\n' >&2
  exit 1
}

if [[ "$ACTION" == "--check" ]]; then
  python3 - <<'PY'
import base64
import json
import os
import urllib.request

port = os.environ['LOCAL_OPENCODE_PORT']
user = os.environ['OPENCODE_SERVER_USERNAME']
password = os.environ['OPENCODE_SERVER_PASSWORD']
token = base64.b64encode((user + ':' + password).encode()).decode()
request = urllib.request.Request(
    f'http://127.0.0.1:{port}/global/health',
    headers={'Authorization': 'Basic ' + token})
with urllib.request.urlopen(request, timeout=10) as response:
    health = json.load(response)
if response.status != 200 or health.get('healthy') is not True:
    raise SystemExit('Authenticated OpenCode health check failed')
print('Authenticated OpenCode health check passed.')
PY
  exit 0
fi

if [[ "$ACTION" == "--smoke" ]]; then
  OPENSHELL_BIN="${OPENSHELL_BIN:-$(command -v openshell || true)}"
  [[ -n "$OPENSHELL_BIN" ]] || { printf 'ERROR: openshell is not on PATH.\n' >&2; exit 1; }
  export OPENSHELL_BIN
  python3 "$REPO_DIR/skills/deploy-opencode-openshell-017/scripts/server_credentials.py" \
    --runtime "$RUNTIME_JSON" --sandbox "$SANDBOX" --dir "$REMOTE_DIR" \
    --port "$LOCAL_OPENCODE_PORT" smoke
  exit 0
fi

command -v opencode >/dev/null 2>&1 || { printf 'ERROR: opencode CLI is not on PATH.\n' >&2; exit 1; }
exec opencode attach "http://127.0.0.1:$LOCAL_OPENCODE_PORT" --dir "$REMOTE_DIR"
