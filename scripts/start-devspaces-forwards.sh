#!/usr/bin/env bash
set -euo pipefail
umask 077

# Restore the two local tunnels used by the OpenShell 0.1.7 Dev Spaces client.
# This expects oc authentication, the OpenShell CLI/config, and the gateway's
# mTLS client files to have already been provisioned in this Dev Spaces home.
# Select the intended gateway with `openshell gateway select` before running.

NS="${NS:-opencode-internal}"
RELEASE="${RELEASE:-openshell-opencode-017}"
WORKSPACE="${WORKSPACE:-default}"
SANDBOX="${SANDBOX:-opencode-server}"
LOCAL_GATEWAY_PORT="${LOCAL_GATEWAY_PORT:-18081}"
GATEWAY_SERVICE_PORT="${GATEWAY_SERVICE_PORT:-8080}"
LOCAL_OPENCODE_PORT="${LOCAL_OPENCODE_PORT:-4096}"

if [[ -z "${OPENSHELL_BIN:-}" ]]; then
  OPENSHELL_BIN="$(command -v openshell || true)"
fi
if [[ -z "$OPENSHELL_BIN" && -x "$HOME/.local/bin/openshell" ]]; then
  OPENSHELL_BIN="$HOME/.local/bin/openshell"
fi

if [[ -z "${XDG_CONFIG_HOME:-}" ]]; then
  XDG_CONFIG_HOME="${OPENSHELL_CONFIG_HOME:-$HOME/.config}"
fi
export XDG_CONFIG_HOME
OPENCODE_AUTH_FILE="${OPENCODE_AUTH_FILE:-$XDG_CONFIG_HOME/opencode-remote/auth.env}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

command -v oc >/dev/null 2>&1 || fail "oc is not installed or is not on PATH."
command -v python3 >/dev/null 2>&1 || fail "python3 is required for the local port check."
command -v ps >/dev/null 2>&1 || fail "ps is required to identify a stale gateway tunnel."
[[ -n "$OPENSHELL_BIN" && -x "$OPENSHELL_BIN" ]] || fail "OpenShell CLI not found. Set OPENSHELL_BIN or install it at ~/.local/bin/openshell."
OC_BIN="$(command -v oc)"

oshell() {
  "$OPENSHELL_BIN" --workspace "$WORKSPACE" "$@"
}

oc whoami >/dev/null 2>&1 || fail "oc is not logged in. Log in to the cluster used by this Dev Spaces workspace, then rerun this script."
printf 'Cluster: %s\n' "$(oc whoami --show-server)"
printf 'Project: %s\n' "$NS"
oc -n "$NS" get service "$RELEASE" >/dev/null 2>&1 \
  || fail "Cannot find service/$RELEASE in namespace $NS, or this identity cannot read it."

tcp_port_open() {
  python3 - "$1" <<'PY' >/dev/null 2>&1
import socket
import sys

sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

STATE_DIR="$HOME/.cache/opencode-devspaces"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
LOG_FILE="$STATE_DIR/gateway-port-forward.log"

gateway_ready() {
  oshell status >/dev/null 2>&1
}

gateway_ready_after_retry() {
  for _ in 1 2 3; do
    if gateway_ready; then
      return 0
    fi
    sleep 1
  done
  return 1
}

gateway_tunnel_pids() {
  ps -eo pid=,comm=,args= | awk \
    -v ns="$NS" \
    -v release="$RELEASE" \
    -v ports="$LOCAL_GATEWAY_PORT:$GATEWAY_SERVICE_PORT" \
    '$2 == "oc" && index($0, "-n " ns " ") && index($0, "port-forward") && index($0, "svc/" release) && index($0, ports) { print $1 }'
}

start_gateway_tunnel() {
  printf 'Starting gateway port-forward on 127.0.0.1:%s...\n' "$LOCAL_GATEWAY_PORT"
  nohup "$OC_BIN" -n "$NS" port-forward --address 127.0.0.1 \
    "svc/$RELEASE" "$LOCAL_GATEWAY_PORT:$GATEWAY_SERVICE_PORT" \
    >"$LOG_FILE" 2>&1 </dev/null &
}

if tcp_port_open "$LOCAL_GATEWAY_PORT"; then
  printf 'A listener already uses 127.0.0.1:%s; checking whether it reaches the configured gateway.\n' "$LOCAL_GATEWAY_PORT"
  if ! gateway_ready_after_retry; then
    STALE_PIDS="$(gateway_tunnel_pids)"
    [[ -n "$STALE_PIDS" ]] \
      || fail "Port $LOCAL_GATEWAY_PORT is occupied by an unrecognized process and the gateway is unreachable. Stop that listener manually, then rerun this script."
    printf 'Restarting stale gateway port-forward process(es): %s\n' "${STALE_PIDS//$'\n'/ }"
    while IFS= read -r pid; do
      [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done <<<"$STALE_PIDS"
    for _ in $(seq 1 10); do
      if ! tcp_port_open "$LOCAL_GATEWAY_PORT"; then
        break
      fi
      sleep 1
    done
    if tcp_port_open "$LOCAL_GATEWAY_PORT"; then
      fail "Stale gateway tunnel did not release port $LOCAL_GATEWAY_PORT. Stop its process manually, then rerun this script."
    fi
    start_gateway_tunnel
  fi
else
  start_gateway_tunnel
fi

gateway_ready=0
for _ in $(seq 1 30); do
  if gateway_ready; then
    gateway_ready=1
    break
  fi
  sleep 1
done

if [[ "$gateway_ready" != 1 ]]; then
  printf 'OpenShell could not reach/authenticate to the selected active gateway.\n' >&2
  printf 'Check the Dev Spaces oc login, saved OpenShell mTLS config, and tunnel log: %s\n' "$LOG_FILE" >&2
  oshell status >&2 || true
  exit 1
fi

printf 'Selected active OpenShell gateway is connected and authenticated.\n'

# Recreate only this user's local OpenCode forward so the script also repairs
# a stale forwarding process left behind by a terminal restart.
oshell forward stop "$LOCAL_OPENCODE_PORT" "$SANDBOX" >/dev/null 2>&1 || true
oshell forward start "127.0.0.1:$LOCAL_OPENCODE_PORT" "$SANDBOX" --background
oshell forward list --output json

printf '\nFor an authenticated OpenCode session, run:\n'
printf '  . %q\n' "$OPENCODE_AUTH_FILE"
printf '  opencode attach http://127.0.0.1:%s --dir /sandbox/workspace\n' "$LOCAL_OPENCODE_PORT"
