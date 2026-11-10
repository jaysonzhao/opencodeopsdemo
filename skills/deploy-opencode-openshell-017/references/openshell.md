# OpenCode under OpenShell: MiniMax Global and OpenShift AI MLflow

## Contents

1. Target and runtime qualification
2. Build the agent and trusted supervisor images
3. Reuse or install the gateway
4. Render policy and provision credentials
5. Start the server and attach locally
6. Bring code context into the sandbox
7. Verify inference, tracing and policy
8. Renew credentials, maintain policy and clean up

## 1. Target and runtime qualification

Run OpenCode itself inside an OpenShell-managed sandbox in `opencode-agents`, separate from Dev Spaces. Keep its HTTP server on sandbox loopback port 4096. Reach it through the OpenShell CLI's authenticated forward; do not create a Service or Route to the sandbox's 4096 port. The gateway has its own internal Service on 8080. Local attachment and Dev Spaces attachment use the same client workflow, with the forward running wherever that client runs.

Use one sandbox/provider pair per developer or trusted group. OpenCode Basic auth is not multi-user repository isolation. Treat access to the gateway client certificate as administrative access to this dedicated gateway. Do not distribute it to untrusted Dev Spaces users; use an approved authenticated workspace gateway for those users.

Audited 2026-11-09: starter kit commit `838546a258193667ed79845d70196c274bbff7f7`, OpenCode **1.18.3**, MLflow SDK/plugin source **3.14.0**, and NVIDIA OpenShell chart/CLI/runtime **0.1.2**. This is a pinned adaptation, not a claim that these are the latest versions. The kit's OpenShell README documents older gateway/CLI versions and privileged setup; do not mix that procedure with this capability-free Kubernetes driver. Reuse a compatible gateway; preserve other versions and sandboxes. Qualify another version against its matching source/help before changing the pin.

OpenShift 4.20+ is the requested target, not unconditional proof of compatibility. Require enforced ingress/egress NetworkPolicy, the Agent Sandbox CRD/controller, Landlock ABI >=3 and working nested seccomp user notification. Older kernels may report `legacy read-only` seccomp mode and reject server operations such as peer-address reads with `EOPNOTSUPP`. Require an actual forwarded OpenCode session and API stream to work. A failed runtime check is a blocker; do not grant privileged SCC or weaken Landlock to make it pass. A suitable supported node/runtime or distribution backport may be needed.

Require `oc`, matching `openshell`, OpenSSH, Helm 3, Python 3 + PyYAML, Git, OpenSSL, Skopeo and Podman (or equivalent approved image-build CI). Use `linux/amd64` images here; do not use the pinned x64 native package on arm64. Build access needs Quay, the UBI registry, GitHub, npm and PyPI. Model access needs `api.minimax.io:443`. Inspect existing resources before creating names.

```bash
set -euo pipefail
umask 077
export NS=opencode-agents
export RELEASE=openshell-opencode
export GATEWAY=opencode-pod
export WORKSPACE=default
export SANDBOX=opencode
export SKILL_DIR=/absolute/path/to/this-skill
export LAB_DIR="$PWD/opencode-openshell-lab"
export OPENSHELL_BIN=/absolute/path/to/openshell-0.1.2
export OPENSHELL_CONFIG_HOME="$LAB_DIR/client-config"
export STORAGE_CLASS=YOUR_BLOCK_STORAGE_CLASS
export MLFLOW_NS=redhat-ods-applications
export MLFLOW_SERVICE=mlflow
export MLFLOW_ROLE=mlflow-operator-mlflow-integration
export MLFLOW_TRACKING_URI="https://${MLFLOW_SERVICE}.${MLFLOW_NS}.svc:8443/mlflow"
export MINIMAX_MODEL=YOUR_ENABLED_MINIMAX_MODEL_ID
mkdir -p "$LAB_DIR"
cd "$LAB_DIR"
oshell() { XDG_CONFIG_HOME="$OPENSHELL_CONFIG_HOME" "$OPENSHELL_BIN" --gateway "$GATEWAY" --workspace "$WORKSPACE" "$@"; }
oc whoami --show-server
oc get clusterversion version
oc get nodes -o wide
oc get storageclass
oc -n "$MLFLOW_NS" get svc "$MLFLOW_SERVICE" -o yaml
oc get clusterrole "$MLFLOW_ROLE" -o yaml
oc get crd sandboxes.agents.x-k8s.io
helm list -A
"$OPENSHELL_BIN" --version
```

Set the actual MLflow service, port, path and integration role. `mlflow-integration` is another role name used by installations. Do not substitute namespace `edit`. Use a MiniMax Global key and an exact model ID enabled for that account (for example `MiniMax-M2.7` only if available); no model name or latest-model availability is assumed. The chosen adapter is `@ai-sdk/anthropic`, with **`https://api.minimax.io/anthropic/v1`**; it appends `/messages`. Do not use the China endpoint or `/v1/chat/completions` with this adapter. Verify actual tool calling and model budgets.

For a missing Agent Sandbox controller, review the official core manifest pinned to `v1.0.9`, its images, platform requirements and cluster RBAC before `oc apply`. Reuse an existing healthy served `agents.x-k8s.io/v1beta1` or `v1alpha1`; do not replace shared CRDs or install a duplicate controller. A fresh gateway/controller installation needs platform permissions. Existing gateway reuse does not authorize changing shared gateway trust or security settings.

## 2. Build the agent and trusted supervisor images

The image follows the kit's OpenShell + MLflow variant, but bundles the plugin and dependencies at build time, fixes the executable path, and uses a foreground persistent server. No runtime npm/pip access is allowed. The Node builder and other mutable base tags should also be digest-pinned in controlled CI. The kit's sandbox base is a development/community image; do not label it Red Hat-certified.

```bash
namespace_name=$(oc get ns "$NS" --ignore-not-found -o name)
if [ -z "$namespace_name" ]; then oc new-project "$NS"; fi
mkdir -p image supervisor-image
cp "$SKILL_DIR/assets/openshell/Containerfile" "$SKILL_DIR/assets/openshell/start.py" \
   "$SKILL_DIR/assets/openshell/check_trace.py" image/
export BASE_TAG=quay.io/aipcc/agentic-ci/openshell:0.3.27
export BASE_IMAGE="${BASE_TAG%:*}@$(skopeo inspect --format '{{.Digest}}' "docker://$BASE_TAG")"
export OPENCODE_TAG=YOUR_REGISTRY/YOUR_PROJECT/opencode-openshell:1.18.3-mlflow3.14
podman build --platform linux/amd64 --build-arg BASE_IMAGE="$BASE_IMAGE" -t "$OPENCODE_TAG" image/
podman push "$OPENCODE_TAG"
export OPENCODE_IMAGE="${OPENCODE_TAG%:*}@$(skopeo inspect --format '{{.Digest}}' "docker://$OPENCODE_TAG")"
export PYTHON_BIN=$(podman run --rm --entrypoint /usr/bin/readlink "$OPENCODE_IMAGE" -f /usr/bin/python3)
podman run --rm --entrypoint /opt/opencode/bin/opencode "$OPENCODE_IMAGE" --version
```

Record both image digests and the actual `PYTHON_BIN`; policy matches the executable, not a launcher symlink. This image installs the official `opencode-linux-x64-baseline@1.18.3` native package to `/opt/opencode/bin/opencode`. Verify pull credentials for the gateway/helper and sandbox identities if using a private registry. Do not grant cross-namespace pull or runtime access broadly.

For the internal MLflow service, its certificate is normally signed by the OpenShift service CA. **The OpenShell supervisor makes the upstream TLS connection**, so putting that CA only in the agent image is insufficient. Build a same-version supervisor image that adds the service CA to its existing public trust store. Keep OpenShell's injected agent trust variables intact; never set `NODE_TLS_REJECT_UNAUTHORIZED=0`, insecure MLflow TLS, or `tls: skip`.

```bash
oc -n "$NS" apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: opencode-service-ca
  namespace: ${NS}
  annotations: {service.beta.openshift.io/inject-cabundle: 'true'}
EOF
# Wait for injection; this exports public trust anchors, not a private key.
for attempt in $(seq 1 30); do
  oc -n "$NS" get configmap opencode-service-ca -o jsonpath='{.data.service-ca\.crt}' > supervisor-image/service-ca.crt
  if [ -s supervisor-image/service-ca.crt ]; then break; fi
  sleep 2
done
test -s supervisor-image/service-ca.crt
openssl crl2pkcs7 -nocrl -certfile supervisor-image/service-ca.crt -out /dev/null
cp "$SKILL_DIR/assets/openshell/Containerfile.supervisor-ca" supervisor-image/Containerfile
export STOCK_SUPERVISOR=ghcr.io/nvidia/openshell/supervisor:0.1.2
export STOCK_SUPERVISOR_IMAGE="${STOCK_SUPERVISOR%:*}@$(skopeo inspect --format '{{.Digest}}' "docker://$STOCK_SUPERVISOR")"
export SUPERVISOR_TAG=YOUR_REGISTRY/YOUR_PROJECT/openshell-supervisor:0.1.2-service-ca
podman build --platform linux/amd64 --build-arg SUPERVISOR_IMAGE="$STOCK_SUPERVISOR_IMAGE" \
  -t "$SUPERVISOR_TAG" supervisor-image/
podman push "$SUPERVISOR_TAG"
export SUPERVISOR_IMAGE="${SUPERVISOR_TAG%:*}@$(skopeo inspect --format '{{.Digest}}' "docker://$SUPERVISOR_TAG")"
```

If using an existing MLflow HTTPS gateway URL with publicly trusted certificates, the stock same-version supervisor may suffice. For a private enterprise CA, export the actual signing chain instead. Verify the selected URL's certificate and path. CA changes require rebuilding the supervisor trust image, updating only the owned gateway and stop/starting its affected sandboxes. Never alter a shared supervisor image setting without its operator's authorization.

## 3. Reuse or install the gateway

For an existing qualified 0.1.2 gateway that trusts the MLflow endpoint, use its approved endpoint, workspace and client credentials; skip installation and certificate extraction. Do not overwrite its values or client registration. For a fresh dedicated gateway, generate values and render before installation:

```bash
python3 - <<'PY'
import os, re, yaml
image=os.environ['SUPERVISOR_IMAGE']
if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}',image): raise SystemExit('Require a verified supervisor image digest')
repository,digest=image.split('@');registry,repository=repository.split('/',1)
values={'fullnameOverride':os.environ['RELEASE'],'global':{'image':{'tag':'0.1.2'}},
 'podSecurityContext':None,'securityContext':{'runAsNonRoot':True,'runAsUser':None,
 'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
 'supervisor':{'image':{'registry':registry,'repository':repository,'digest':digest}},
 'server':{'disableTls':False,'tls':{'enableMtls':True},
 'auth':{'allowUnauthenticatedUsers':True},'telemetryEnabled':False,
 'workspaceStorageClass':os.environ['STORAGE_CLASS'],'workspaceDefaultStorageSize':'10Gi'},
 'pkiInitJob':{'enabled':True},'networkPolicy':{'enabled':True},'openshiftRoute':{'enabled':False}}
with open('gateway-values.yaml','w') as f: yaml.safe_dump(values,f,sort_keys=False)
PY
chmod +x "$SKILL_DIR/scripts/gateway_storage.py"
helm template "$RELEASE" oci://ghcr.io/nvidia/openshell/helm-chart --version 0.1.2 \
  -n "$NS" -f gateway-values.yaml --set agentSandbox.preflight.enabled=false \
  --post-renderer "$SKILL_DIR/scripts/gateway_storage.py" > gateway-rendered.yaml
# Inspect rendered RBAC, images, SCC fields, TLS and both storage classes first.
helm install "$RELEASE" oci://ghcr.io/nvidia/openshell/helm-chart --version 0.1.2 \
  -n "$NS" -f gateway-values.yaml --post-renderer "$SKILL_DIR/scripts/gateway_storage.py" --wait --timeout 10m
oc -n "$NS" rollout status "statefulset/$RELEASE" --timeout=300s
```

The post-renderer sets the fresh gateway's RWO claim class; this chart otherwise relies on the cluster default. It also sets the sandbox PVC class through supported values. Do not use NFS for gateway/OpenCode SQLite state, mutate bound PVC classes, or change the shared default StorageClass. `agentSandbox.preflight.enabled=false` is **only** for offline rendering, never the live install.

This local-evaluation mode permits the gateway's developer principal only behind mandatory mTLS and a loopback tunnel. Verify a client without its certificate is rejected. Retain OIDC/RBAC if reusing a managed gateway; do not change it to this local mode. Export only the dedicated fresh gateway client bundle, into a new private directory:

```bash
export TLS_DIR="$OPENSHELL_CONFIG_HOME/openshell/gateways/$GATEWAY/mtls"
test ! -e "$TLS_DIR"
mkdir -p "$TLS_DIR"
oc -n "$NS" get secret openshell-client-tls -o jsonpath='{.data.ca\.crt}' | base64 -d > "$TLS_DIR/ca.crt"
oc -n "$NS" get secret openshell-client-tls -o jsonpath='{.data.tls\.crt}' | base64 -d > "$TLS_DIR/tls.crt"
oc -n "$NS" get secret openshell-client-tls -o jsonpath='{.data.tls\.key}' | base64 -d > "$TLS_DIR/tls.key"
chmod 600 "$TLS_DIR"/*
test -s "$TLS_DIR/ca.crt" && test -s "$TLS_DIR/tls.crt" && test -s "$TLS_DIR/tls.key"
```

In a separate terminal, run `oc -n "$NS" port-forward --address 127.0.0.1 "svc/$RELEASE" 18080:8080`. Keep it running. In the original terminal:

```bash
XDG_CONFIG_HOME="$OPENSHELL_CONFIG_HOME" "$OPENSHELL_BIN" gateway add https://127.0.0.1:18080 --local --name "$GATEWAY"
oshell status
oshell sandbox list
oshell provider list
```

A successful `status` is not enough: require real sandbox exec and the 4096 forward below. Some OpenShift environments have SSH relay stalls through `oc port-forward`. Diagnose that control transport separately; do not expose the raw agent port or disable mTLS. An approved existing authenticated gateway Route is an alternative transport; creating a new external Route is outside this internal baseline.

## 4. Render policy and provision credentials

```bash
python3 "$SKILL_DIR/scripts/render_openshell.py" --out generated --namespace "$NS" \
  --model "$MINIMAX_MODEL" --mlflow-uri "$MLFLOW_TRACKING_URI" --python-binary "$PYTHON_BIN" \
  --context 32768 --output 8192
oc -n "$NS" create serviceaccount opencode-tracer --dry-run=client -o yaml | oc apply -f -
oc -n "$NS" create rolebinding opencode-openshell-mlflow --clusterrole="$MLFLOW_ROLE" \
  --serviceaccount="$NS:opencode-tracer" --dry-run=client -o yaml | oc apply -f -
oshell profile lint -f generated/minimax-profile.yaml
oshell profile lint -f generated/mlflow-profile.yaml
oshell profile import -f generated/minimax-profile.yaml
oshell profile import -f generated/mlflow-profile.yaml
python3 "$SKILL_DIR/scripts/credentials.py" init-secrets
python3 "$SKILL_DIR/scripts/credentials.py" create-providers
```

Replace example token budgets with verified limits. Profiles are workspace-scoped; omit `--global`. If these IDs/providers already exist, inspect and compare ownership before reuse; import/create is intentionally create-only. For an intentional profile edit, export it, preserve `resource_version`, and use `profile update`. A partial provisioning failure can leave a created provider; inspect it before retrying rather than deleting it automatically.

The helper prompts for the MiniMax key and stores it with model settings in OpenShift `Secret/opencode-minimax`. It creates a distinct application password in `Secret/opencode-openshell-auth`. It reads Secret values into the control process and uses bare `--credential KEY` arguments; actual keys are never in argv, image layers, generated policy, or transcript. OpenShell stores provider credentials and injects opaque handles into the agent environment. The same key is never used as the OpenCode server password.

MLflow uses a separate **`opencode-tracer`** identity authorized in workspace `${NS}`. The sandbox does not get a Kubernetes SA token mount or kubeconfig. The helper issues a two-hour TokenRequest, records its actual expiry in the OpenShell provider, and supplies `MLFLOW_TRACKING_TOKEN` as a handle. This is a renewable, time-limited external credential, **not automatic Kubernetes projected-token rotation**. Section 8 supplies renewal; do not claim unattended long-lived tracing without an operator-managed renewal process.

The generated effective access is deliberately narrow:

| Caller | Destination | Allowed work |
|---|---|---|
| `/opt/opencode/bin/opencode` | `api.minimax.io:443`, `/anthropic/v1/**` | POST messages and token counting; MiniMax handle resolves only here |
| OpenCode and the observed Python executable | Exact MLflow host/port, its `/api/**` prefix | Experiment lookup/create, trace export/search and MLflow artifact proxy methods |
| Other binaries/destinations | None by default | Denied, including curl to MiniMax, arbitrary websites, Git and package registries |
| Sandbox filesystem | `/sandbox`, temporary paths | Writable working tree, config, SQLite/WAL/state and caches; executable/plugin directories are read-only |

Base policy and profile endpoints match, so provider policy composition does not widen this design. Inspect both; a gateway global policy replaces the base and can change the effective result. TLS inspection stays enabled. MLflow export requires POST and sometimes PUT/PATCH, so a `read-only` MLflow rule is insufficient. Add an object-storage endpoint only if the actual MLflow export protocol requires it, with its own narrow credentials and trust.

## 5. Start the server and attach locally

Use a new sandbox name or inspect/adopt the intended existing one. Do not apply the legacy standalone Deployment, 4096 Service or namespace ingress policy to these managed pods. OpenShell's CNI fence must remain effective.

```bash
oshell sandbox create --name "$SANDBOX" --from "$OPENCODE_IMAGE" \
  --provider opencode-minimax --provider opencode-mlflow --no-auto-providers \
  --policy generated/policy.yaml --cpu 2 --memory 4Gi --detach -- \
  python3 /opt/opencode-launch/start.py
oshell sandbox get "$SANDBOX" -o json
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- id
python3 "$SKILL_DIR/scripts/credentials.py" stage
oshell policy get "$SANDBOX" --base
oshell policy get "$SANDBOX" --full
oshell sandbox provider status "$SANDBOX" opencode-minimax --wait
oshell sandbox provider status "$SANDBOX" opencode-mlflow --wait
```

Wait for exec readiness, then stage within ten minutes. The canonical foreground process waits for its two config files; staging sends only nonsecret runtime settings and the application's Basic-auth password over the OpenShell exec channel. Real MiniMax/MLflow credentials never enter those files. If the ten-minute wait expires, start the retained sandbox to reopen that window, wait for exec readiness and immediately stage the files; do not delete its PVC. Do not use `oc exec` or `su` to start OpenCode outside OpenShell's supervised execution path.

Install the matching OpenCode **1.18.3** CLI on the local machine. OpenShell forwarding and the OpenCode CLI must run on the same machine (or in the same Dev Spaces workspace). Start the application tunnel:

```bash
oshell forward start 127.0.0.1:4096 "$SANDBOX" --background
oshell forward list --output json
python3 "$SKILL_DIR/scripts/credentials.py" smoke --dir /sandbox/workspace
python3 "$SKILL_DIR/scripts/credentials.py" attach --dir /sandbox/workspace
```

The last command securely supplies the local login variables and runs **`opencode attach http://127.0.0.1:4096 --dir /sandbox/workspace`**. It requires read access only to the application-login Secret, not the model Secret. Alternatively, enter a securely delivered server password locally into `OPENCODE_SERVER_PASSWORD` and set `OPENCODE_SERVER_USERNAME=opencode`; then use the same `opencode attach` command. Do not put the password in CLI flags/history. Stop a background forward with `oshell forward stop 4096 "$SANDBOX"`. An egress allow rule for localhost:4096 is unnecessary.

## 6. Bring code context into the sandbox

`--dir` refers to files **inside the sandbox**. The client does not stream unsaved editor buffers or synchronize files automatically. Preserve the previous Dev Spaces separation and Git review workflow. The default policy leaves Git egress closed; stage a reviewed commit through OpenShell instead:

```bash
# From the local/Dev Spaces repository; choose a committed baseline and inspect it.
git status --short
export REVIEWED_COMMIT=$(git rev-parse HEAD)
mkdir -p "$LAB_DIR/repo-export/app"
git archive "$REVIEWED_COMMIT" | tar -x -C "$LAB_DIR/repo-export/app"
oshell sandbox upload "$SANDBOX" "$LAB_DIR/repo-export/app" /sandbox/workspace
oshell sandbox exec -n "$SANDBOX" --workdir /sandbox/workspace/app --no-login-shell -- sh -ec '
  test ! -e .git
  git init -q
  git config user.name OpenCode
  git config user.email opencode@openshift.local
  git add .
  git commit -qm "Reviewed input baseline"
'
python3 "$SKILL_DIR/scripts/credentials.py" attach --dir /sandbox/workspace/app
```

Use a new destination for the initial import. Upload merges and can overwrite files; inspect existing changes before any repeat. The Git archive deliberately excludes `.git` and uncommitted/untracked files; submodule contents and LFS objects need an explicit reviewed export. Inspect tracked files for secrets before sending them to the sandbox/model. To include local uncommitted edits, stage a reviewed patch or explicit safe file set; never upload the entire home directory or credential files.

Keep `AGENTS.md`, architecture notes and build/test instructions in the exported project. Example request: “Read AGENTS.md and src/api; add pagination using the current patterns, run the documented tests, and show the diff. Do not commit or push.” Add necessary compilers/test tools and dependencies to the image at build time; missing runtime registries remain denied. Export changes with `oshell sandbox download "$SANDBOX" /sandbox/workspace/app ./reviewed-output`, review the diff and tests, then apply selected changes to the original Dev Spaces branch. Git cloning/pushing is optional: grant exact repository hosts, paths and actual Git HTTPS helper binaries through a separate profile only when that workflow is requested.

## 7. Verify inference, tracing and policy

Require all of the following before reporting a working deployment; preparation and syntax checks are not live acceptance:

1. Inspect admitted SCCs, PVCs, gateway/runtime/image versions and sandbox qualification logs. Require enforced Landlock and seccomp; inspect the actual listener mode. Require no anonymous gateway control access. Confirm CNI policies do not allow a direct path around the supervisor.
2. The local smoke check must pass forwarded health, correct remote directory, anonymous `/session` rejection (401), and a real MiniMax response. OpenCode's port must not be exposed by a separate Service/Route. Test interactive attachment and streamed responses too.
3. Take the printed marker and query MLflow through the supervised identity:

```bash
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- \
  python3 /opt/opencode-launch/check_trace.py PASTE_PRINTED_MARKER
oc get consolelink mlflow -o jsonpath='{.spec.href}{"\n"}'
```

Open the UI, choose workspace `${NS}` and experiment `opencode-openshell-traces`; inspect the matching request, model, tool spans and token metadata where emitted. Creating an experiment alone does not prove trace export. Keep `serve` alive for the plugin's idle-event flush. RHOAI 3.5 documents an MLflow 3.13 backend and SDK >=3.11; this kit-derived SDK is 3.14.0, so an actual matching trace is the compatibility gate. Traces can contain code/prompts/tool output; apply the project's data policy.

4. Test code context with a benign unique marker in an exported project file. Ask OpenCode to read it and make a small change; independently check the server diff and tests, then download it.
5. Test controls through `openshell sandbox exec`, not a raw pod/root shell. Observe `oshell term` or sandbox logs and correlate destination, binary and action:

```bash
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- sh -c 'echo allowed > /sandbox/policy-ok'
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- curl --connect-timeout 5 --max-time 10 https://example.com
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- curl --connect-timeout 5 --max-time 10 https://api.minimax.io/anthropic/v1/messages
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- python3 -c 'import os; print({k:os.environ.get(k, "").startswith("openshell:") for k in ("MINIMAX_API_KEY","MLFLOW_TRACKING_TOKEN")})'
```

The write should pass; both curl requests should have policy-denial evidence, while a genuine OpenCode model call passes. Placeholder checks print booleans only; verify they differ from the real control-side secrets without displaying either value. HTTP 401/403 alone is not proof of a policy denial. For filesystem negative tests, use a disposable image-owned writable-by-UID directory omitted from the policy and compare its DAC/SELinux permissions before attributing the denial to Landlock; ordinary `/etc` permission failure is insufficient.

6. Finish active work, stop/start the sandbox, reestablish the forward, and check previous session/history and workspace files. Then renew MLflow credentials as below and require a new matching trace. Do not claim token renewal from a successful provider update alone.

## 8. Renew credentials, maintain policy and clean up

**MLflow renewal:** the requested lifetime is two hours; Kubernetes may return another lifetime, so use the printed actual expiry. Renew well before expiry (for example after 60 minutes only if the issued lifetime exceeds that). Complete/idle active prompts first:

```bash
python3 "$SKILL_DIR/scripts/credentials.py" rotate-mlflow
oshell sandbox stop "$SANDBOX"
oshell sandbox start "$SANDBOX"
oshell sandbox provider status "$SANDBOX" opencode-mlflow --wait
oshell forward start 127.0.0.1:4096 "$SANDBOX" --background
python3 "$SKILL_DIR/scripts/credentials.py" smoke --dir /sandbox/workspace/app
```

Check a fresh matching trace. A static OpenShell provider update does not refresh the credential reference already held by a running OpenCode process. Stop/start retains the PVC and canonical command; it disconnects current clients and forwards. Do not delete/recreate the sandbox merely to rotate a token. For unattended operations, an operator-owned renewal controller/job must perform this sequence during idle windows with narrowly scoped TokenRequest and gateway permissions; it is not installed or claimed by this baseline.

**MiniMax rotation:** update `opencode-minimax` through approved secret tooling without printing its values; keep model settings consistent with runtime.json. Run `credentials.py rotate-minimax`, then the same stop/start/forward sequence. For a server-password change, update the dedicated login Secret and run `credentials.py stage` before stop/start; refresh local clients. Preserve other Secret values during updates.

**Policy updates:** `policy update` changes network rules live; `policy set` replaces the whole file. Export/inspect current base policy and retain runtime-added filesystem paths. Use a dry run for targeted changes, followed by `--wait`, then verify effective policy and real requests. For example, a requested curl-only read test can be added and later removed:

```bash
oshell policy update "$SANDBOX" --rule-name approved-read-demo --binary /usr/bin/curl \
  --add-endpoint example.com:443:read-only:rest:enforce --dry-run
# Apply only if this exact additional access is intended, using --wait instead of --dry-run.
# Remove the added rule afterward:
oshell policy update "$SANDBOX" --remove-rule approved-read-demo --wait
```

That example does not authorize MiniMax credential use by curl. Editing a provider endpoint/binary also requires the provider profile change, with its resource version. Do not broaden the MiniMax host scope to make a placeholder resolve. Network changes can close streams, requiring reconnection. Filesystem/process/Landlock changes need recreation under the version's lifecycle rules; export state and prepare a separate replacement sandbox before any requested migration. Never silently delete a working sandbox.

| Symptom | Check |
|---|---|
| Forward fails but gateway status passes | SSH relay/control transport, sandbox readiness, loopback listener and actual seccomp listener mode; `EOPNOTSUPP` can indicate kernel limitations |
| MiniMax 401 / unresolved handle | Global API key/plan/model, Anthropic URL, exact binary and path binding, attached provider readiness; restart after a static credential change |
| MLflow TLS failure | The supervisor's upstream CA store and exact service DNS, then the injected agent proxy CA; never disable verification |
| MLflow 403 | `opencode-tracer` subject, actual integration ClusterRole, workspace RoleBinding and `MLFLOW_WORKSPACE` |
| MLflow 401 after some time | Actual TokenRequest expiry and stale running-process handle; renew and restart, then prove a new trace |
| No traces, experiment exists | Plugin resolution, POST/PUT/PATCH denies, idle flush, workspace header and SDK/backend compatibility |
| SQLite/FTS error | Writable `/sandbox` parents, SCC/fsGroup, block PVC, disk capacity and WAL/temp paths; retain database/state |
| Stale code | Correct sandbox `--dir`, export commit and explicit file transfer; editor buffers are not automatically shared |

Leave the working sandbox available. For requested removal, export code/state first, stop owned local forwards, and delete only the named sandbox after acknowledging its PVC deletion behavior. Remove its two provider instances and workspace profiles only when unreferenced. Retain source Secrets, tracer identity and CA ConfigMap unless their deletion is requested. Do not uninstall a shared gateway/controller, delete a namespace, or remove unrelated resources. Preserve the legacy standalone server/PVC until a requested migration has passed acceptance.
