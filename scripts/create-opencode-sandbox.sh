#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_DIR="$REPO_DIR/skills/deploy-opencode-openshell-017"
RUNTIME_JSON="${RUNTIME_JSON:-$REPO_DIR/deployments/cluster-9blcx/runtime.json}"
NS="${NS:-opencode-internal}"
WORKSPACE="${WORKSPACE:-default}"
GATEWAY="${GATEWAY:-openshell-opencode-017}"
SOURCE_SANDBOX="${SOURCE_SANDBOX:-opencode-server}"
SANDBOX="${SANDBOX:-opencode-server-2}"
PIN_SOURCE_NODE="${PIN_SOURCE_NODE:-false}"
MODEL_PROVIDER="opencode-model"
MLFLOW_PROVIDER="opencode-mlflow"
# Keep the sandbox small enough for shared OpenShift worker pools; override on
# clusters with different capacity. By default, let the scheduler choose a node.
CPU="${CPU:-500m}"
MEMORY="${MEMORY:-4Gi}"

OPENSHELL_BIN="${OPENSHELL_BIN:-$(command -v openshell || true)}"
if [[ -z "$OPENSHELL_BIN" && -x "$HOME/.local/bin/openshell" ]]; then
  OPENSHELL_BIN="$HOME/.local/bin/openshell"
fi
[[ -n "$OPENSHELL_BIN" && -x "$OPENSHELL_BIN" ]] || {
  printf 'ERROR: OpenShell CLI 0.1.7 was not found.\n' >&2
  exit 1
}
command -v oc >/dev/null 2>&1 || { printf 'ERROR: oc is not on PATH.\n' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { printf 'ERROR: python3 is not on PATH.\n' >&2; exit 1; }
command -v grep >/dev/null 2>&1 || { printf 'ERROR: grep is not on PATH.\n' >&2; exit 1; }
[[ -r "$RUNTIME_JSON" ]] || { printf 'ERROR: runtime settings not readable: %s\n' "$RUNTIME_JSON" >&2; exit 1; }

if [[ -z "${OPENSHELL_CONFIG_HOME:-}" ]]; then
  OPENSHELL_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
fi
export OPENSHELL_CONFIG_HOME OPENSHELL_BIN GATEWAY WORKSPACE NS
export OPENSHELL_COLOR=never

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

(( ${#SANDBOX} <= 19 )) || fail "Sandbox name is too long (maximum 19 characters): $SANDBOX"
[[ "$PIN_SOURCE_NODE" == true || "$PIN_SOURCE_NODE" == false ]] \
  || fail "Set PIN_SOURCE_NODE to true or false."

oshell() {
  "$OPENSHELL_BIN" --gateway "$GATEWAY" --workspace "$WORKSPACE" "$@"
}

version="$("$OPENSHELL_BIN" --version 2>&1)"
[[ "$version" == *0.1.7* ]] || fail "Use OpenShell 0.1.7; found: $version"
oc whoami >/dev/null 2>&1 || fail "oc is not logged in from this control terminal."
oc -n "$NS" get service openshell-opencode-017 >/dev/null 2>&1 \
  || fail "Cannot read the OpenShell gateway Service in namespace $NS."
oshell status >/dev/null || fail "Selected gateway $GATEWAY is unreachable or unauthenticated."

SANDBOX_NAMES="$(oshell sandbox list --names)"
printf '%s\n' "$SANDBOX_NAMES" | grep -Fqx "$SOURCE_SANDBOX" \
  || fail "Source sandbox $SOURCE_SANDBOX is not present in workspace $WORKSPACE."
if printf '%s\n' "$SANDBOX_NAMES" | grep -Fqx "$SANDBOX"; then
  fail "Sandbox $SANDBOX already exists; refusing to overwrite it."
fi

PROVIDER_NAMES="$(oshell provider list --names)"
printf '%s\n' "$PROVIDER_NAMES" | grep -Fqx "$MODEL_PROVIDER" \
  || fail "Required model provider $MODEL_PROVIDER is missing."
printf '%s\n' "$PROVIDER_NAMES" | grep -Fqx "$MLFLOW_PROVIDER" \
  || fail "Required MLflow provider $MLFLOW_PROVIDER is missing."

SOURCE_POD="${SOURCE_POD:-${WORKSPACE}--${SOURCE_SANDBOX}}"
POD_PHASE="$(oc -n "$NS" get pod "$SOURCE_POD" -o jsonpath='{.status.phase}' 2>/dev/null)"
[[ "$POD_PHASE" == Running ]] || fail "Source sandbox pod $SOURCE_POD is not Running."
SERVER_IMAGE="$(oc -n "$NS" get pod "$SOURCE_POD" -o jsonpath='{.spec.containers[0].image}')"
[[ "$SERVER_IMAGE" =~ @sha256:[a-f0-9]{64}$ ]] \
  || fail "Source sandbox image is not pinned by digest: $SERVER_IMAGE"
DRIVER_CONFIG_ARGS=()
if [[ "$PIN_SOURCE_NODE" == true ]]; then
  SOURCE_NODE="$(oc -n "$NS" get pod "$SOURCE_POD" -o jsonpath='{.spec.nodeName}')"
  NODE_HOSTNAME="$(oc get node "$SOURCE_NODE" -o jsonpath='{.metadata.labels.kubernetes\.io/hostname}')"
  [[ -n "$NODE_HOSTNAME" ]] || fail "Cannot resolve the source node hostname."
  DRIVER_CONFIG_JSON="$(python3 - "$NODE_HOSTNAME" <<'PY'
import json
import sys
print(json.dumps({'kubernetes': {'pod': {'node_selector': {'kubernetes.io/hostname': sys.argv[1]}}}}))
PY
)"
  DRIVER_CONFIG_ARGS=(--driver-config-json "$DRIVER_CONFIG_JSON")
  printf 'Pinning %s to the source node %s.\n' "$SANDBOX" "$SOURCE_NODE"
else
  printf 'Letting OpenShift schedule %s on an available node.\n' "$SANDBOX"
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/opencode-sandbox.XXXXXX")"
trap 'rm -rf "$TEMP_DIR"' EXIT
POLICY_FILE="$TEMP_DIR/source-base-policy.yaml"
oshell policy get "$SOURCE_SANDBOX" --base --output json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["policy"]))' \
  >"$POLICY_FILE"
python3 - "$POLICY_FILE" <<'PY'
import json
import sys
with open(sys.argv[1]) as f:
    policy = json.load(f)
required = {'version', 'filesystem_policy', 'landlock', 'network_policies'}
if not required.issubset(policy):
    raise SystemExit('Source base policy is missing required fields')
PY

printf 'Creating %s from the existing server image digest.\n' "$SANDBOX"
oshell sandbox create --name "$SANDBOX" --from "$SERVER_IMAGE" \
  --provider "$MODEL_PROVIDER" --provider "$MLFLOW_PROVIDER" --no-auto-providers \
  --policy "$POLICY_FILE" "${DRIVER_CONFIG_ARGS[@]}" \
  --cpu "$CPU" --memory "$MEMORY" --detach -- \
  python3 /opt/opencode-server/start.py

ready=0
for _ in $(seq 1 60); do
  if oshell sandbox exec -n "$SANDBOX" --no-login-shell -- id >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
[[ "$ready" == 1 ]] || fail "Sandbox exec did not become ready; inspect logs for $SANDBOX."

python3 "$SKILL_DIR/scripts/server_credentials.py" \
  --runtime "$RUNTIME_JSON" --sandbox "$SANDBOX" stage
oshell sandbox provider status "$SANDBOX" "$MODEL_PROVIDER" --wait --timeout 60
oshell sandbox provider status "$SANDBOX" "$MLFLOW_PROVIDER" --wait --timeout 60
oshell sandbox get "$SANDBOX" --output json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get("sandbox",{}); print("Sandbox:",s.get("name")); print("Phase:",s.get("phase")); print("Workspace:",s.get("workspace"))'
printf '\nNext, run scripts/attach-devspaces-opencode.sh --check (or --smoke).\n'
