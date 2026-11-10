# Internal OpenCode server inside OpenShell

## Contents

1. Scope, versions and inputs
2. Build and publish the server image
3. Install or reuse the internal gateway
4. Qualify the worker runtime
5. Configure the later-defined model and existing MLflow service
6. Start, connect and use the server
7. Verify tracing, isolation and persistence
8. Renew credentials and maintain the deployment

## 1. Scope, versions and inputs

Deploy OpenCode's server inside an OpenShell-managed pod sandbox on OpenShift 4.20+. Keep the gateway in its own project, separate from Dev Spaces. Use only internal access: client -> authenticated gateway connection -> OpenShell forward -> sandbox loopback OpenCode server. Do not expose 4096 directly or create a public Route. A local browser UI is not included by `opencode serve`; the supported clients here are the OpenCode CLI and authenticated HTTP API.

Source-review baseline, 2026-11-10: starter kit commit `36b2678d1a8e71db25e59fc138d0c7fda3d50a50`, OpenShell **0.1.7** commit `5d25211b25a8fc887e8d5c606d57d6ab22f1d76c`, OpenCode **1.18.3**, MLflow SDK and plugin source **v3.14.0**. The starter kit's older 0.0.86/0.0.58 privileged walkthrough is not the runtime procedure here. Preserve existing 0.1.2/0.0.x gateways and use a new gateway for this track.

The reported 4.20.39 workers all run RHCOS 9.6 kernel `5.14.0-570.141.1`; OpenShell 0.1.2 failed ordinary socket accept there. OpenShell 0.1.7 no longer writes workload memory in its broker, documents normal accepted-socket peer semantics, and adds an unprivileged `SO_BINDTODEVICE` probe. Test the released implementation on a current worker. Do not declare all 5.14 kernels supported, demand the obsolete 0.1.2 listener-mode check, or claim a 5.19 version alone satisfies Landlock ABI 3. A failing runtime needs a platform-supported runtime/node solution, not weakened isolation.

Require `oc`, Helm 3, verified matching OpenShell CLI, OpenSSH, Python 3 + PyYAML, Git, OpenSSL, Skopeo and Podman or approved image-build CI. The supplied image is **linux/amd64**; qualify a separate native image for arm64. Build-time registries are separate from agent runtime egress. Do not put API keys in build arguments, source, manifests or terminal history.

```bash
set -euo pipefail
umask 077
export SKILL_DIR=/absolute/path/to/extracted-skill
export LAB_DIR="$PWD/opencode-internal-lab"
export NS=opencode-internal
export RELEASE=openshell-opencode-017
export GATEWAY=opencode-internal-017
export WORKSPACE=default
export SANDBOX=opencode-server
export OPENSHELL_BIN=/absolute/path/to/verified/openshell-0.1.7
export OPENSHELL_CONFIG_HOME="$LAB_DIR/client-config"
export STORAGE_CLASS=YOUR_APPROVED_BLOCK_STORAGE_CLASS
export CANDIDATE_NODE=YOUR_EXISTING_WORKER_NAME
mkdir -p "$LAB_DIR"
cd "$LAB_DIR"
oshell() { XDG_CONFIG_HOME="$OPENSHELL_CONFIG_HOME" "$OPENSHELL_BIN" --gateway "$GATEWAY" --workspace "$WORKSPACE" "$@"; }
oc whoami --show-server
oc get clusterversion version
oc get nodes -o custom-columns=NAME:.metadata.name,KERNEL:.status.nodeInfo.kernelVersion,OS:.status.nodeInfo.osImage
oc get storageclass "$STORAGE_CLASS"
oc get crd sandboxes.agents.x-k8s.io
helm list -A
"$OPENSHELL_BIN" --version
test ! -e settings.json
cp "$SKILL_DIR/assets/server/settings.example.json" settings.json
```

Obtain the CLI from the official `v0.1.7` release and verify its published checksum/signature before running it. Reuse the installed healthy Agent Sandbox CRDs/controller. If missing, inspect the pinned chart's required controller version, image support and RBAC and have the platform operator install that dependency; do not silently replace shared CRDs. Inspect the namespace, gateway and names before adopting existing resources. For a new dedicated installation, create the namespace with `oc create namespace "$NS"`. Keep one sandbox/provider set per trusted developer or group; gateway client credentials confer broad gateway control and are not credentials to distribute to untrusted tenants.

Leave null values in `settings.json` until known. The renderer rejects incomplete settings and unknown fields. Infrastructure/image preparation can proceed without a model endpoint or key; provider creation, OpenCode startup and inference must wait for those actual values.

## 2. Build and publish the server image

Build the plugin from the reviewed MLflow source tag, install it locally in the image, and use the fixed native OpenCode binary. This avoids runtime npm plugin downloads and executable-path policy mismatches. The upstream sandbox base is a community image, not a Red Hat-certified application image. Resolve and record base/builder image digests in controlled CI.

```bash
mkdir -p image
cp "$SKILL_DIR/assets/server/Containerfile" "$SKILL_DIR/assets/server/"*.py image/
export BASE_TAG=quay.io/aipcc/agentic-ci/openshell:0.3.27
export BASE_IMAGE="${BASE_TAG%:*}@$(skopeo inspect --format '{{.Digest}}' "docker://$BASE_TAG")"
export SERVER_TAG=YOUR_REGISTRY/YOUR_PROJECT/opencode-server:1.18.3-mlflow3.14
podman build --platform linux/amd64 --build-arg BASE_IMAGE="$BASE_IMAGE" -t "$SERVER_TAG" image/
podman push "$SERVER_TAG"
export SERVER_IMAGE="${SERVER_TAG%:*}@$(skopeo inspect --format '{{.Digest}}' "docker://$SERVER_TAG")"
podman run --rm --entrypoint /opt/opencode/bin/opencode "$SERVER_IMAGE" --version
export PYTHON_BIN=$(podman run --rm --entrypoint /usr/bin/readlink "$SERVER_IMAGE" -f /usr/bin/python3)
podman run --rm --entrypoint python3 "$SERVER_IMAGE" -c 'import mlflow; print(mlflow.__version__)'
```

Use the resolved `PYTHON_BIN` in settings. If building with OpenShift BuildConfig instead, set the same build context and base-image argument, use a fresh owned BuildConfig/ImageStream, require Build `Complete`, resolve the published digest independently and verify actual workload `imageID`. Registry HTTP 500 is an image-publication failure, not a socket-policy problem; preserve logs and diagnose registry health/storage. Do not rebuild indefinitely or deploy an unpublished tag. Configure narrowly scoped pull credentials for the gateway/workspace identities if needed.

Add the project's required test/compiler dependencies to this image at build time. Keep Node/plugin dependencies readable under OpenShift's namespace-assigned UID. OpenShell mounts persistent `/sandbox`; the launcher recreates needed directories there. Never depend on a fixed runtime UID 1001 or `chmod 777`.

## 3. Install or reuse the internal gateway

Reuse an existing **qualified 0.1.7** gateway only when its operator permits the workspace, provider profiles and destination CA trust. Retain its managed authentication and scheduling settings. Skip the following installation and certificate extraction for that case. Do not upgrade a shared or older gateway in place.

For the new dedicated gateway, collect the cluster service CA. OpenShell's supervisor connects to the upstream MLflow/model service; trusting only inside the agent is insufficient. This ConfigMap holds public CA certificates only.

```bash
oc -n "$NS" create configmap opencode-service-ca
oc -n "$NS" annotate configmap opencode-service-ca service.beta.openshift.io/inject-cabundle=true
for attempt in $(seq 1 30); do
  oc -n "$NS" get configmap opencode-service-ca -o jsonpath='{.data.service-ca\.crt}' > service-ca.crt
  if [ -s service-ca.crt ]; then break; fi
  sleep 2
done
test -s service-ca.crt
openssl crl2pkcs7 -nocrl -certfile service-ca.crt -out /dev/null
oc -n "$NS" create configmap opencode-destination-ca --from-file=ca.crt=service-ca.crt
cp "$SKILL_DIR/assets/server/gateway-values.yaml" gateway-values.yaml
helm template "$RELEASE" oci://ghcr.io/nvidia/openshell/helm-chart --version 0.1.7 \
  -n "$NS" -f gateway-values.yaml --set fullnameOverride="$RELEASE" \
  --set server.dataStorageClass="$STORAGE_CLASS" --set server.workspaceStorageClass="$STORAGE_CLASS" \
  --set agentSandbox.preflight.enabled=false > gateway-rendered.yaml
# Inspect images, RBAC, Service type, TLS, SCC fields and both block PVCs.
helm install "$RELEASE" oci://ghcr.io/nvidia/openshell/helm-chart --version 0.1.7 \
  -n "$NS" -f gateway-values.yaml --set fullnameOverride="$RELEASE" \
  --set server.dataStorageClass="$STORAGE_CLASS" --set server.workspaceStorageClass="$STORAGE_CLASS" \
  --wait --timeout 10m
oc -n "$NS" rollout status "statefulset/$RELEASE" --timeout=300s
```

The preflight override is for offline rendering only; it is absent from live installation. Set both StorageClasses at installation: the gateway data claim and workspace claims contain SQLite state and need suitable block storage. Do not mutate a bound PVC's class or the cluster default. Use the actual enterprise CA chain too if either destination is signed by a different private authority; concatenate public roots into `ca.crt` before creating the destination ConfigMap. Do not set `tls: skip` or disable certificate verification.

The supplied values keep the local developer principal behind **mandatory mTLS**, with no OIDC configured and no external Route. This is a dedicated trusted-operator gateway. Do not copy `allowUnauthenticatedUsers: true` onto a shared gateway without that client-certificate boundary. Verify a client without its certificate is rejected. Export only this fresh gateway's client bundle into a private new directory:

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

Keep this command running in a second terminal, with the same namespace/release values:

```bash
oc -n "$NS" port-forward --address 127.0.0.1 "svc/$RELEASE" 18081:8080
```

Register the gateway in the original terminal:

```bash
XDG_CONFIG_HOME="$OPENSHELL_CONFIG_HOME" "$OPENSHELL_BIN" gateway add https://127.0.0.1:18081 --local --name "$GATEWAY"
oshell status
oshell sandbox list
```

Port 18081 avoids the earlier gateway's 18080 tunnel. For an in-cluster trusted client, the gateway ClusterIP DNS endpoint can replace the `oc` tunnel if its certificate includes that exact Service DNS name and NetworkPolicy permits the connection. Keep mTLS and the same supervisor-mediated agent forward. Do not disable hostname validation or provide a raw 4096 Service as a shortcut.

Separately from a `set -e` success sequence, test `curl --cacert "$TLS_DIR/ca.crt" --max-time 10 https://127.0.0.1:18081/` without a client certificate. Require the TLS client-certificate rejection, then successful authenticated `oshell status`. An HTTP response, even 404, is not proof of mandatory client-certificate protection. Resolve an unexpected result before provisioning credentials.

## 4. Qualify the worker runtime

Test a separate sandbox on one actual worker before adding credentials. `Ready` and `ss -ltn` do not prove accept, forwarding or streaming. `server.drivers.kubernetes.allowDriverConfig` is enabled only on this dedicated gateway for the explicit node selection; preserve existing managed-gateway admission rules.

```bash
python3 - <<'PY'
import json,os,re,subprocess,yaml
image=os.environ['SERVER_IMAGE']
if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}',image):
    raise SystemExit('Use a published immutable image digest')
node=json.loads(subprocess.run(['oc','get','node',os.environ['CANDIDATE_NODE'],'-o','json'],check=True,text=True,capture_output=True).stdout)
driver={'kubernetes':{'pod':{'node_selector':{'kubernetes.io/hostname':node['metadata']['labels']['kubernetes.io/hostname']}}}}
open('driver-config.json','w').write(json.dumps(driver))
policy={'version':1,'filesystem_policy':{'include_workdir':False,
 'read_only':['/bin','/usr','/lib','/lib64','/etc','/opt','/proc','/dev/urandom'],
 'read_write':['/sandbox','/tmp','/var/tmp','/dev/null']},
 'landlock':{'compatibility':'hard_requirement'},'network_policies':{}}
with open('runtime-policy.yaml','w') as f:yaml.safe_dump(policy,f,sort_keys=False)
PY
export DRIVER_CONFIG_JSON=$(cat driver-config.json)
oshell sandbox create --name opencode-runtime-probe --from "$SERVER_IMAGE" \
  --no-auto-providers --policy runtime-policy.yaml --driver-config-json "$DRIVER_CONFIG_JSON" \
  --cpu 1 --memory 1Gi --detach -- sleep infinity
oshell sandbox get opencode-runtime-probe -o json
oshell logs opencode-runtime-probe -n 100
oc -n "$NS" get pods -o wide
oshell sandbox exec -n opencode-runtime-probe --no-login-shell -- \
  python3 /opt/opencode-server/runtime_probe.py
```

Require active Landlock/seccomp/task-memory/socket-binding qualification, matching trusted 0.1.7 image IDs, intended node placement and all three socket checks true. The probe intentionally leaves `full_runtime_qualification: false`: it covers socket behavior only. Complete HTTP, streaming, egress and CNI tests below. If it fails, retain the exact stage/errno, version and placement. Do not add Linux capabilities, privileged/anyuid SCC, syscall interposers, or unconfined seccomp. Preserve the probe for diagnosis rather than deleting the old working environment.

## 5. Configure the later-defined model and existing MLflow service

Fill `settings.json` when values are supplied. It is nonsecret. The default provider implements OpenAI-compatible **Chat Completions**, streaming and tools; Responses-only backends require another reviewed adapter. Do not add the API operation itself to the base URL.

| Setting | Required value |
|---|---|
| `namespace` | The dedicated sandbox project, matching `$NS` |
| `model_base_url` | Exact API base, normally `https://MODEL_SERVICE.MODEL_NS.svc:PORT/v1` |
| `model_id` | Exact served model ID; slashes in IDs are allowed |
| `model_context`, `model_output` | Verified limits, with output strictly below context |
| `model_secret`, `model_secret_key` | Existing or intentionally created Secret name and key in `$NS`; default `opencode-model-api` / `API_KEY` |
| `allow_internal_http_model` | Default false; explicit true permits HTTP only for `.svc` / `.svc.cluster.local` model DNS, with credentials transmitted in plaintext on that internal hop |
| `mlflow_uri` | Observed HTTPS Service URL including its deployment prefix, e.g. `https://mlflow.redhat-ods-applications.svc:8443/mlflow`; verify, do not assume |
| `mlflow_workspace` | Existing authorized OpenShift project; normally `$NS`, but may be a separate project |
| `mlflow_experiment` | Experiment name for these traces |
| `python_binary` | Resolved `$PYTHON_BIN`, e.g. the image's actual `/usr/bin/python3.x` |

Keep real credentials out of this file. Prefer HTTPS for the model; if an operator explicitly selects internal HTTP, retain exact DNS/port/path restrictions. This flag is not an exemption for public HTTP endpoints. For non-bearer API authentication, inspect the service contract and adapt the credential/header configuration deliberately.

Discover the existing OpenShift AI 3.5 MLflow Service, port, prefix, certificate, backend version and integration ClusterRole. Do not install another MLflow server or change the shared deployment. RHOAI projects map to MLflow workspaces; the role binding belongs in the **target MLflow workspace**.

```bash
oc get svc -A | rg mlflow
oc get clusterrole -o name | rg mlflow-integration
# Set these to observed values, not guessed names.
export MLFLOW_NS=YOUR_MLFLOW_SERVICE_NAMESPACE
export MLFLOW_SERVICE=YOUR_MLFLOW_SERVICE_NAME
export MLFLOW_ROLE=YOUR_OBSERVED_MLFLOW_INTEGRATION_CLUSTERROLE
export MLFLOW_WORKSPACE=YOUR_AUTHORIZED_MLFLOW_WORKSPACE
oc -n "$MLFLOW_NS" get svc "$MLFLOW_SERVICE" -o yaml
oc get clusterrole "$MLFLOW_ROLE" -o yaml
python3 "$SKILL_DIR/scripts/render_server.py" --settings settings.json --out generated
oc -n "$NS" create serviceaccount opencode-tracer
oc -n "$MLFLOW_WORKSPACE" create rolebinding opencode-internal-tracing \
  --clusterrole="$MLFLOW_ROLE" --serviceaccount="$NS:opencode-tracer"
oshell profile lint -f generated/model-profile.yaml
oshell profile lint -f generated/mlflow-profile.yaml
oshell profile import -f generated/model-profile.yaml
oshell profile import -f generated/mlflow-profile.yaml
python3 "$SKILL_DIR/scripts/server_credentials.py" init-secrets
python3 "$SKILL_DIR/scripts/server_credentials.py" create-providers
```

Confirm `settings.json` workspace equals `$MLFLOW_WORKSPACE` before binding. Existing SA/RoleBinding/profile/provider names require ownership and value comparison before adoption; create-only commands must not overwrite them. The helper retains existing Secrets and imports only the selected key. If no model Secret exists, it prompts without echo and creates one. It creates a separate application-login Secret. Never reuse the model key as the server password. A partial provider-creation failure may leave the model provider created; inspect state before retrying, not delete-and-recreate by default.

Both profiles use endpoint-bound `stable_placeholder: true`. Supporting 0.1.7 CLI/gateway/supervisor/runtime are required; profile lint/import checks support. The sandbox gets opaque values for `MODEL_API_KEY` and `MLFLOW_TRACKING_TOKEN`, not the real API key or Kubernetes JWT. The token comes from a separate external `opencode-tracer` TokenRequest; no SA token mount or Kubernetes API egress is granted to the agent. Do not substitute namespace `edit` for the observed integration role.

The effective policy permits only OpenCode POST `<model-prefix>/chat/completions` and GET `<model-prefix>/models`, and necessary MLflow experiment/trace/artifact-proxy operations from OpenCode and the actual Python executable. Both exact trace collection roots and descendant paths are present. A `read-only` preset would block inference/trace POST. Keep runtime npm, arbitrary web and Git denied. Inspect `policy get --full`: provider composition or a gateway-global policy can change effective access.

## 6. Start, connect and use the server

Create a new server sandbox on the same qualified node. Do not start OpenCode with `oc exec`, root or `su` outside the supervised path. The canonical launcher waits up to ten minutes for its nonsecret settings and application password, then creates the MLflow experiment and runs `opencode serve` in the foreground.

```bash
oshell sandbox create --name "$SANDBOX" --from "$SERVER_IMAGE" \
  --provider opencode-model --provider opencode-mlflow --no-auto-providers \
  --policy generated/policy.yaml --driver-config-json "$DRIVER_CONFIG_JSON" \
  --cpu 2 --memory 4Gi --detach -- python3 /opt/opencode-server/start.py
oshell sandbox get "$SANDBOX" -o json
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- id
python3 "$SKILL_DIR/scripts/server_credentials.py" stage
oshell sandbox provider status "$SANDBOX" opencode-model --wait --timeout 60
oshell sandbox provider status "$SANDBOX" opencode-mlflow --wait --timeout 60
oshell policy get "$SANDBOX" --base
oshell policy get "$SANDBOX" --full
oshell logs "$SANDBOX" -n 100
```

Wait for exec readiness before staging. If staging times out, correct the cause, start the retained sandbox and stage immediately; do not delete its persistent volume. An MLflow initialization failure intentionally prevents this traced-server workflow from claiming readiness. Investigate the exact TLS/RBAC/policy failure.

Install matching OpenCode 1.18.3 on the client. Run the OpenShell forward and OpenCode client on the same machine or in the same Dev Spaces workspace:

```bash
oshell forward start 127.0.0.1:4096 "$SANDBOX" --background
oshell forward list --output json
python3 "$SKILL_DIR/scripts/server_credentials.py" smoke --dir /sandbox/workspace
python3 "$SKILL_DIR/scripts/server_credentials.py" attach --dir /sandbox/workspace
```

The attach helper reads only the application-login Secret and supplies `OPENCODE_SERVER_USERNAME` and `OPENCODE_SERVER_PASSWORD` locally, then executes `opencode attach http://127.0.0.1:4096 --dir /sandbox/workspace`. For clients without Secret-read permission, deliver that distinct password through the approved secret channel and set those two environment variables securely; run the same attach command. Do not put a password in flags, the URL or chat. Keep client gateway authentication intact. Stop only the owned application tunnel with `oshell forward stop 4096 "$SANDBOX"`.

The helper resolves its default runtime file from `$LAB_DIR/generated/runtime.json`, so attachment still works after changing into a source repository. Use `--runtime /absolute/path/to/runtime.json` to select a different prepared configuration explicitly.

`--dir` selects a path **on the server**; it does not synchronize local editor buffers. To send a reviewed commit from a local/Dev Spaces repo, use a new destination and inspect tracked files for credentials first:

```bash
# Run in the source repository with the deployment environment still set.
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
python3 "$SKILL_DIR/scripts/server_credentials.py" attach --dir /sandbox/workspace/app
```

The archive excludes uncommitted files, submodule contents and LFS objects; handle any required additions explicitly. Upload merges may overwrite files, so inspect before repeating. Include `AGENTS.md`, architecture notes and documented tests. Example prompt: “Read AGENTS.md and this application, add a small feature using the existing patterns, run its tests, and show the diff.” Download for review with `oshell sandbox download "$SANDBOX" /sandbox/workspace/app ./reviewed-output`. Review the diff/tests before applying it to the source branch. Add exact Git hosts/helper binaries only when remote Git use is requested.

## 7. Verify tracing, isolation and persistence

Use the marker printed by `smoke` to find the actual request trace, then open the existing MLflow UI and select `mlflow_workspace` / `mlflow_experiment`:

```bash
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- \
  python3 /opt/opencode-server/check_trace.py PASTE_PRINTED_MARKER
oc get consolelink mlflow -o jsonpath='{.spec.href}{"\n"}'
```

The plugin exports on session-idle events; allow a bounded flush interval. An experiment, server health or provider Ready status is not a trace. OpenShift AI 3.5 documents an MLflow 3.13 backend and SDK >=3.11; this starter-kit adaptation uses SDK/plugin source 3.14.0. Verify actual trace API/artifact compatibility. Inspect a second tool-using request for code/tool/model spans and available usage metadata. Trace storage may include prompts/code/tool results; use the authorized workspace.

This policy expects MLflow's artifact proxy. A workspace-specific `MLflowConfig` may instead require direct S3 access. Inspect that configuration before deployment. If direct artifact storage is required, add only the actual storage endpoint, CA and dedicated credential binding after reviewing its protocol; do not assume the MLflow-only profile covers it or turn on unrestricted egress.

Require actual interactive streaming over the forward, not only a completed JSON response. The smoke helper checks health, remote directory, anonymous `/session` rejection and a real marked response. Check the admitted SCC, runtime versions/image IDs and CNI enforcement independently; there must be no direct ingress path around the supervisor. Run negative tests individually because failure is expected:

```bash
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- sh -c 'echo allowed > /sandbox/policy-check'
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- curl --connect-timeout 5 --max-time 10 https://example.com
oshell sandbox exec -n "$SANDBOX" --no-login-shell -- python3 -c 'import os; print({k:os.environ.get(k, "").startswith("openshell:") for k in ("MODEL_API_KEY","MLFLOW_TRACKING_TOKEN")})'
```

Also attempt the configured model endpoint with unauthorized `curl`; do not supply a credential. Correlate policy denial logs and binary/destination instead of interpreting an arbitrary upstream 401/403 as a policy success. Require model access through OpenCode and denial through the unauthorized binary. Do not print full environments, provider values, Secret objects or `/config` responses.

After an approved idle lifecycle test, stop/start the retained sandbox using the matching CLI, reconnect and verify a previously created file/session plus a new marked response/trace. Record sandbox identity, PVC and image version before/after. Do not delete/recreate as a generic persistence test.

## 8. Renew credentials and maintain the deployment

Run the renewal controller on a trusted host with valid `oc` and gateway credentials, from `$LAB_DIR`, in another terminal. One writer per sandbox/provider is allowed; the helper uses a host-local lock, but operators must also avoid writers on multiple hosts.

```bash
python3 "$SKILL_DIR/scripts/server_credentials.py" renew-mlflow
python3 "$SKILL_DIR/scripts/server_credentials.py" renew-loop
```

The loop issues TokenRequests, checks the actual subject/expiry, updates the same provider value, waits for activation and verifies unchanged server identity. It schedules the next renewal before expiry. Native stable placeholders let the existing MLflow client keep its opaque string; the supervisor resolves the new credential. No client auth patch, real JWT file or routine server restart is required. Retain the loop and its control authentication; it is not a scheduler installation, and an expired operator login will eventually cause tracing failure.

Acceptance requires a new matching trace **after the previous JWT's actual expiry**, while the same OpenCode process and sandbox remain alive. Immediate provider readiness does not prove this. If renewal fails, expose only sanitized stage/error metadata and expiry, restore the control path, and verify a fresh trace. Do not disable auth or substitute a long-lived administrator token.

For model key **value-only** rotation, update the same OpenShift Secret through the approved secret mechanism, then run `server_credentials.py rotate-model` and verify inference from the same server. Changing model endpoint bindings, replacing providers or moving to another API adapter is a separate migration: render to a new directory, compare policy/profile/runtime, preserve profile resource versions, and plan the new process/context deliberately. Stable references do not persist across provider replacement or endpoint-binding changes.

CA rotation: update `opencode-destination-ca`, restart/redeploy the owned gateway to load it, then explicitly stop/start every affected sandbox; the CA source is not hot-reloaded. Overlap old/new roots during rollover. Do not use the old 0.1.2 custom supervisor image or reference-file patch here.

Network policy changes can be updated live using the matching CLI. Export the current base first, preserve all existing rules and inspect the effective policy after application. Filesystem policy is applied at sandbox startup; follow 0.1.7's documented lifecycle and preserve state before any replacement. Never weaken containment to work around SQLite/permission errors.

Report prepared files, source/syntax/render checks and unresolved live gates separately. Clean up only explicitly owned, unneeded test resources after acceptance. Preserve original gateways, model Secrets, client keys, PVCs and worktrees unless their deletion is requested.
