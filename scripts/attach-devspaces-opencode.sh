#!/usr/bin/env bash

# A raw GitHub script can also be piped to `sh` or `bash`. In that case there
# is no local repository for the companion files, so bootstrap a checkout and
# re-enter this script from disk under Bash.
if [ -z "${BASH_SOURCE:-}" ] || [ "${0##*/}" = sh ] || [ "${0##*/}" = bash ]; then
  set -eu
  command -v git >/dev/null 2>&1 || {
    printf 'ERROR: git is required when running this script through sh or stdin.\n' >&2
    exit 1
  }
  command -v bash >/dev/null 2>&1 || {
    printf 'ERROR: bash is required to run the checked-out helper.\n' >&2
    exit 1
  }
  REPO_URL="${OPENCODEOPSDEMO_REPO_URL:-https://github.com/jaysonzhao/opencodeopsdemo.git}"
  CACHE_HOME="${XDG_CACHE_HOME:-${HOME:?HOME is not set}/.cache}"
  BOOTSTRAP_REPO="${OPENCODEOPSDEMO_REPO_DIR:-$CACHE_HOME/opencodeopsdemo}"
  if [ -d "$BOOTSTRAP_REPO/.git" ]; then
    git -C "$BOOTSTRAP_REPO" fetch --quiet origin main
    git -C "$BOOTSTRAP_REPO" merge --ff-only --quiet origin/main || {
      printf 'ERROR: cached repo has local changes; resolve them in %s before retrying.\n' "$BOOTSTRAP_REPO" >&2
      exit 1
    }
  elif [ -e "$BOOTSTRAP_REPO" ]; then
    printf 'ERROR: bootstrap path exists but is not a Git checkout: %s\n' "$BOOTSTRAP_REPO" >&2
    exit 1
  else
    mkdir -p "$(dirname -- "$BOOTSTRAP_REPO")"
    git clone --quiet --depth 1 --branch main "$REPO_URL" "$BOOTSTRAP_REPO"
  fi
  exec bash "$BOOTSTRAP_REPO/scripts/attach-devspaces-opencode.sh" "$@"
fi

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SANDBOX="${SANDBOX:-opencode-server-2}"
WORKSPACE="${WORKSPACE:-default}"
LOCAL_OPENCODE_PORT="${LOCAL_OPENCODE_PORT:-4096}"
LOCAL_GATEWAY_PORT="${LOCAL_GATEWAY_PORT:-18081}"
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
[[ "$LOCAL_GATEWAY_PORT" =~ ^[0-9]+$ ]] \
  && (( LOCAL_GATEWAY_PORT > 0 && LOCAL_GATEWAY_PORT < 65536 )) \
  || { printf 'ERROR: invalid LOCAL_GATEWAY_PORT.\n' >&2; exit 1; }

if [[ -z "${OPENSHELL_CONFIG_HOME:-}" ]]; then
  OPENSHELL_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
fi
OPENCODE_AUTH_FILE="${OPENCODE_AUTH_FILE:-$OPENSHELL_CONFIG_HOME/opencode-remote/auth.env}"

command -v oc >/dev/null 2>&1 || {
  printf 'ERROR: oc is required to provision this workspace. Log in to the cluster and retry.\n' >&2
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  printf 'ERROR: python3 is required to provision protected credentials.\n' >&2
  exit 1
}
command -v openshell >/dev/null 2>&1 || {
  printf 'ERROR: OpenShell 0.1.7 is not on PATH. Install the matching CLI, then retry.\n' >&2
  exit 1
}
[[ "$(openshell --version 2>/dev/null)" == 'openshell 0.1.7' ]] || {
  printf 'ERROR: this gateway requires OpenShell CLI 0.1.7.\n' >&2
  exit 1
}

oc whoami >/dev/null 2>&1 || {
  printf 'ERROR: oc is not logged in. Log in to the cluster used by this Dev Spaces workspace, then retry.\n' >&2
  exit 1
}

mkdir -p "$(dirname -- "$OPENCODE_AUTH_FILE")"
chmod 700 "$(dirname -- "$OPENCODE_AUTH_FILE")"
[[ ! -L "$OPENCODE_AUTH_FILE" ]] || {
  printf 'ERROR: refusing to use a symlink as the protected login file: %s\n' "$OPENCODE_AUTH_FILE" >&2
  exit 1
}
if [[ ! -f "$OPENCODE_AUTH_FILE" || ! -r "$OPENCODE_AUTH_FILE" ]]; then
  if ! oc -n "$NS" get secret opencode-server-auth -o json | python3 -c '
import base64
import json
import os
import shlex
import sys
import tempfile

target = sys.argv[1]
data = json.load(sys.stdin)["data"]
values = {
    name: base64.b64decode(data[name], validate=True).decode("utf-8")
    for name in ("OPENCODE_SERVER_USERNAME", "OPENCODE_SERVER_PASSWORD")
}
if not all(values.values()):
    raise SystemExit("server login Secret contains an empty value")
fd, temporary = tempfile.mkstemp(prefix=".auth.env.", dir=os.path.dirname(target))
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        for name, value in values.items():
            stream.write(f"{name}={shlex.quote(value)}\n")
    os.replace(temporary, target)
    os.chmod(target, 0o600)
except BaseException:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
' "$OPENCODE_AUTH_FILE"; then
    printf 'ERROR: could not provision %s from Secret/opencode-server-auth in %s.\n' \
      "$OPENCODE_AUTH_FILE" "$NS" >&2
    printf 'Check oc access to that named Secret or provision the protected file through your approved secret channel.\n' >&2
    exit 1
  fi
  printf 'Created protected OpenCode login file from Secret/opencode-server-auth.\n'
else
  chmod 600 "$OPENCODE_AUTH_FILE"
fi

# A new Dev Spaces home has no OpenShell profile. Reuse the existing gateway
# client Secret; it contains the client cert trusted by this 0.1.7 gateway.
TLS_DIR="$OPENSHELL_CONFIG_HOME/openshell/gateways/$GATEWAY/mtls"
mkdir -p "$TLS_DIR"
chmod 700 "$TLS_DIR"
TLS_SOURCE_MARKER="$TLS_DIR/.source-openshell-dashboard-client-tls"
for tls_file in "$TLS_DIR/ca.crt" "$TLS_DIR/tls.crt" "$TLS_DIR/tls.key"; do
  [[ ! -L "$tls_file" ]] || {
    printf 'ERROR: refusing to use a symlink in the gateway certificate directory: %s\n' "$tls_file" >&2
    exit 1
  }
done
if [[ ! -f "$TLS_SOURCE_MARKER" || ! -r "$TLS_DIR/ca.crt" || ! -r "$TLS_DIR/tls.crt" || ! -r "$TLS_DIR/tls.key" ]]; then
  if ! oc -n "$NS" get secret openshell-dashboard-client-tls -o json | python3 -c '
import base64
import json
import os
import sys
import tempfile

target = sys.argv[1]
data = json.load(sys.stdin)["data"]
names = ("ca.crt", "tls.crt", "tls.key")
decoded = {name: base64.b64decode(data[name], validate=True) for name in names}
temporary = []
try:
    for name, value in decoded.items():
        fd, path = tempfile.mkstemp(prefix="." + name + ".", dir=target)
        temporary.append((path, os.path.join(target, name)))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
    for path, final in temporary:
        os.replace(path, final)
except BaseException:
    for path, _ in temporary:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    raise
' "$TLS_DIR"; then
    printf 'ERROR: could not provision gateway mTLS files from Secret/openshell-dashboard-client-tls in %s.\n' "$NS" >&2
    printf 'Check oc access to that named Secret or restore the approved gateway client bundle.\n' >&2
    exit 1
  fi
  : > "$TLS_SOURCE_MARKER"
  chmod 600 "$TLS_SOURCE_MARKER"
  printf 'Created private OpenShell client files from Secret/openshell-dashboard-client-tls.\n'
else
  chmod 600 "$TLS_DIR/ca.crt" "$TLS_DIR/tls.crt" "$TLS_DIR/tls.key"
fi

export OPENSHELL_CONFIG_HOME OPENCODE_AUTH_FILE NS GATEWAY WORKSPACE SANDBOX LOCAL_OPENCODE_PORT LOCAL_GATEWAY_PORT
export XDG_CONFIG_HOME="$OPENSHELL_CONFIG_HOME"
export OPENSHELL_COLOR=never

GATEWAY_ENDPOINT="https://127.0.0.1:$LOCAL_GATEWAY_PORT"
GATEWAY_LIST="$(openshell gateway list --output json)"
if ! printf '%s' "$GATEWAY_LIST" | python3 -c 'import json,sys; name=sys.argv[1]; sys.exit(0 if any(item.get("name")==name for item in json.load(sys.stdin)) else 1)' "$GATEWAY"; then
  openshell gateway add "$GATEWAY_ENDPOINT" --local --name "$GATEWAY"
else
  printf '%s' "$GATEWAY_LIST" | python3 -c 'import json,sys; name,endpoint=sys.argv[1:]; matches=[item for item in json.load(sys.stdin) if item.get("name")==name]; sys.exit(0 if len(matches)==1 and matches[0].get("endpoint")==endpoint and matches[0].get("type")=="local" and matches[0].get("auth")=="mtls" else 1)' "$GATEWAY" "$GATEWAY_ENDPOINT" || {
    printf 'ERROR: registered gateway %s does not match %s with local mTLS. Review it with openshell gateway list.\n' "$GATEWAY" "$GATEWAY_ENDPOINT" >&2
    exit 1
  }
  openshell gateway select "$GATEWAY" >/dev/null
fi

"$SCRIPT_DIR/start-devspaces-forwards.sh"

set -a
# Load only the separate OpenCode server-login values for the client session.
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
