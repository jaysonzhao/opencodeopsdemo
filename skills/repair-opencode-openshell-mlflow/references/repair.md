# Diagnose and repair the deployment

## Contents

1. Scope and evidence
2. Gateway, runtime and forwarding
3. MiniMax and policy
4. MLflow trust, RBAC and trace delivery
5. Filesystem and code context
6. Acceptance and rollback

## 1. Scope and evidence

The user has not supplied an exact error. Collect it before choosing a repair branch; do not assume that every known issue exists. This skill supports the existing separate-project OpenCode/OpenShell design. It also contains an explicit improvement for MLflow credential expiry, independent of any unknown error.

Run from the trusted control host. Use the same private client configuration and actual resource names from the deployment. Keep diagnostics private and redact prompts/code from logs before sharing them. Do not dump Secret objects, full environments, `/config`, provider credential responses or gateway client keys.

```bash
set -euo pipefail
umask 077
export NS=opencode-agents
export RELEASE=openshell-opencode
export GATEWAY=opencode-pod
export WORKSPACE=default
export SANDBOX=opencode
export OPENSHELL_BIN=/absolute/path/to/openshell-0.1.2
export OPENSHELL_CONFIG_HOME=/absolute/path/to/existing/client-config
export DEPLOY_SKILL_DIR=/absolute/path/to/extracted/deployment-skill
export REPAIR_SKILL_DIR=/absolute/path/to/extracted/repair-skill
export LAB_DIR=/absolute/path/to/existing/opencode-openshell-lab
oshell() { XDG_CONFIG_HOME="$OPENSHELL_CONFIG_HOME" "$OPENSHELL_BIN" --gateway "$GATEWAY" --workspace "$WORKSPACE" "$@"; }
cd "$LAB_DIR"
"$OPENSHELL_BIN" --version
oc whoami --show-server
oc get clusterversion version
oc get nodes -o custom-columns=NAME:.metadata.name,KERNEL:.status.nodeInfo.kernelVersion,OS:.status.nodeInfo.osImage
oc -n "$NS" get pods,svc,pvc
oshell status
oshell sandbox get "$SANDBOX" -o json
oshell sandbox provider list "$SANDBOX"
oshell policy get "$SANDBOX" --base -o json > policy-before.json
oshell policy get "$SANDBOX" --full -o json > policy-effective-before.json
oshell logs "$SANDBOX" -n 100
```

Run diagnostic commands individually when a prior one fails. Save the first failed command, its exit code, relevant status/reason and a short sanitized log excerpt. Record current image digests, sandbox identity, node placement, PVCs and Git changes. A `policy get -o json` response is an API envelope; extract its actual policy using the pinned CLI/schema before using a whole-policy replacement. Do not pass the envelope back as a policy file.

## 2. Gateway, runtime and forwarding

| Evidence | Repair |
|---|---|
| Gateway unreachable | Check Service/EndpointSlice, gateway pod readiness and the local control tunnel. Restore the existing tunnel; do not create a raw-port agent Route. |
| mTLS / hostname failure | Verify the active gateway entry, intended CA, matching client certificate/key and endpoint SAN. Preserve managed OIDC/RBAC. Do not disable verification. |
| Gateway status works, exec fails | Inspect supervisor/sandbox readiness, actual qualification output and SSH relay path. Do not diagnose this as an OpenCode password error. |
| Landlock/seccomp qualification failure | Verify kernel features/backports and runtime profile with the platform operator. An OpenShift version or kernel version string alone is not proof. |
| `legacy read-only`, `EOPNOTSUPP`, accept/getpeername failure | The OpenShell 0.1.2 seccomp broker may reject peer-address writes on older kernels. Move/recreate only the owned workload on a supported, qualified runtime/node using supported driver scheduling after preserving state. Do not change the shared node kernel automatically or relax SCC/seccomp. |
| Exec works, server absent | Check foreground command, staging files, plugin dependencies, writable state and startup logs. Use the appropriate recovery below. |
| Local port in use | Stop only the known stale OpenShell forward or choose a separate client machine/terminal. Never kill an unknown process by port. |

For the existing internal gateway, restore this tunnel in its own terminal, using the variables above:

```bash
oc -n "$NS" port-forward --address 127.0.0.1 "svc/$RELEASE" 18080:8080
```

The existing gateway registration must point at the intended `https://127.0.0.1:18080` and use the matching mTLS bundle. Do not overwrite registration as a generic repair.

```bash
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- id
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- sh -c 'command -v opencode; opencode --version; ss -ltn'
oshell forward list --output json
oshell forward start 127.0.0.1:4096 "$SANDBOX" --background
python3 "$DEPLOY_SKILL_DIR/scripts/credentials.py" smoke --dir /sandbox/workspace
python3 "$DEPLOY_SKILL_DIR/scripts/credentials.py" attach --dir /sandbox/workspace
```

The direct equivalent after securely supplying the application-login variables is `opencode attach http://127.0.0.1:4096 --dir /sandbox/workspace`. Its directory is remote. Basic-auth 401 is separate from gateway mTLS. No egress policy rule for localhost:4096 is needed.

If the original launcher timed out waiting for configuration: start the retained sandbox, wait for exec readiness, immediately run the existing `credentials.py stage`, then inspect startup. If an infrastructure error requires stop/start under the pinned driver, fix the underlying reason first and use that documented lifecycle. Keep the PVC. A configured running server should be restarted only during an intended repair window, not to test whether a restart happens to hide the error.

For the repaired launcher described in `renewal.md`, configuration staging is followed by `renew_mlflow.py --bootstrap`; otherwise it waits for a fresh reference file and eventually fails. Do not use the old rotate-and-restart helper on the repair provider.

## 3. MiniMax and policy

Check the configured **Global** endpoint, `@ai-sdk/anthropic` adapter, actual enabled model, tool calling and verified budgets. `/anthropic/v1/messages` must be a POST from the real native binary `/opt/opencode/bin/opencode`. Keep the provider credential header `x-api-key` and environment key `MINIMAX_API_KEY`. A China key, incompatible plan/model or OpenAI route is not repaired by broadening the firewall.

Use provider readiness and a real model call to distinguish a key/model failure from a policy deny. Read only boolean placeholder checks:

```bash
oshell sandbox provider status "$SANDBOX" opencode-minimax --wait --timeout 60
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- python3 -c 'import os; print({k:os.environ.get(k, "").startswith("openshell:") for k in ("MINIMAX_API_KEY","MLFLOW_TRACKING_TOKEN")})'
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- curl --connect-timeout 5 --max-time 10 https://example.com
```

Expect a policy deny for arbitrary websites and for curl to the model API. Correlate actual denial logs; upstream HTTP 401/403 alone is not policy evidence. Run expected-negative commands separately from `set -e` sequences.

Export the relevant profile (`oshell profile export opencode-rhoai-mlflow -o yaml > mlflow-profile-before.yaml`) before changing it. Preserve `resource_version`; update using `profile update`, never overwrite a shared profile with create/import. Inspect all affected provider instances. Make the smallest change to both base policy and provider profile, wait for application, and recheck `--full`. Preserve runtime-added paths in whole-policy changes. Do not use wildcard hosts/binaries or grant arbitrary shell/Python internet access as a workaround.

MLflow L7 rules need **exact collection paths** as well as descendants, especially POST `<prefix>/api/3.0/mlflow/traces`. An endpoint selector ending `/**` and an L7 `allow.path` glob do not have identical matching behavior in 0.1.2. The current deployment renderer includes both; an older locally edited policy may not.

If MiniMax's key itself is rotated, its running OpenCode provider still uses a static reference. This repair's automatic reload is for MLflow only. Perform MiniMax rotation in a planned idle rollout, or independently implement/test a provider-specific dynamic auth mechanism. Do not claim MLflow's fix rotates model credentials automatically.

## 4. MLflow trust, RBAC and trace delivery

| Evidence | Repair |
|---|---|
| TLS error / unknown CA | Install the actual service/enterprise CA in the **supervisor's upstream** trust store using the prior same-version supervisor CA image. Preserve agent proxy CA variables. Trust or supervisor image changes may need a planned sandbox restart. |
| MLflow 401 at expiry | For immediate original-runtime recovery, issue a fresh token and restart the idle server as documented. For no routine restart, install the separate client patch and renewal helper in `renewal.md`. |
| MLflow 403 | Verify `opencode-tracer`, the actual integration ClusterRole, its RoleBinding in the authorized workspace and `MLFLOW_WORKSPACE`. Wrong workspace is not fixed by broader namespace permissions. |
| MLflow 404 | Verify Service port and deployment path prefix; do not append model `/v1` or an OTLP URL to the tracking URI. |
| Experiment exists, trace absent | Verify plugin loading, real compiled patched core resolution, session idle flush, POST trace root, artifact PUT/GET paths and SDK/backend compatibility. An experiment is insufficient. |
| Direct-to-storage attempts denied | Keep MLflow's multipart bypass disabled for this policy. Enable it only with explicit storage-host trust, exact egress and credentials; the default policy permits the MLflow artifact proxy. |

```bash
export MLFLOW_NS=redhat-ods-applications
export MLFLOW_SERVICE=mlflow
export MLFLOW_ROLE=mlflow-operator-mlflow-integration
oc -n "$MLFLOW_NS" get svc "$MLFLOW_SERVICE" -o yaml
oc -n "$NS" get sa opencode-tracer
oc -n "$NS" get rolebinding opencode-openshell-mlflow -o yaml
oc get clusterrole "$MLFLOW_ROLE" -o yaml
```

Use observed role/service names. Repair only the owned binding, retaining a dedicated identity. Do not run `oc whoami --show-token`, print the token Secret, copy a kubeconfig into the sandbox or grant `edit`.

Send a uniquely marked prompt with the existing smoke helper, then run the image's supervised `check_trace.py <marker>`. Inspect the trace in the existing OpenShift AI UI/workspace. For expiry acceptance, repeat after the old credential's real expiry and confirm unchanged server identity, as described in `renewal.md`.

## 5. Filesystem and code context

Keep `/sandbox` writable and persistent on block storage. Check admitted SCC/fsGroup, PVC capacity, parent ownership and SQLite WAL/temp paths. Do not delete a database, replace an unknown PVC, set `chmod -R 777`, grant root/privileged or share one SQLite state directory across multiple servers. Ordinary `/etc` DAC failure does not demonstrate Landlock enforcement.

Inspect only metadata first:

```bash
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- sh -c 'id; df -h /sandbox; ls -ld /sandbox /sandbox/.local /sandbox/.local/share /sandbox/.opencode-setup; test -w /sandbox'
```

The prior runtime-independent checks remain useful: correct `--dir`, committed baseline, `AGENTS.md`, architecture notes, dependencies and build/test commands. Unsaved Dev Spaces buffers are not present remotely.

OpenShell 0.1.2 `sandbox upload` takes separate source and destination arguments. For a fresh destination, a named local directory retains its basename:

```bash
# Inspect the export and the remote destination before writing.
oshell sandbox upload "$SANDBOX" "$LAB_DIR/repo-export/app" /sandbox/workspace
# Result: /sandbox/workspace/app. Repeat uploads may overwrite matching files.
```

Do not pass `local:remote` to this command; that shorthand belongs to `sandbox create --upload`. Do not copy live SQLite/WAL files as a backup. Use the application's supported session export or a consistent SQLite backup while idle, validate it, and retain the original PVC. Do not promise automatic session-history migration to a new sandbox.

## 6. Acceptance and rollback

Record exactly which branch was repaired and why. Require real forwarded streaming inference, model/tool behavior, correct code context, matching MLflow trace and negative policy evidence. Preserve the intended endpoint restrictions and no-direct-Service exposure. A successful health check does not establish kernel compatibility under load or trace delivery after expiry.

Rollback only changed owned resources using the recorded policy/profile revisions and previous image digest. Restore the intended gateway registration/forward when appropriate. A new repair sandbox uses its own MLflow provider; it can be stopped while the original sandbox/provider remains available. Keep both PVCs and reviewed work until the user requests deletion. No rollback automatically restores Secret contents or converts a database schema.
