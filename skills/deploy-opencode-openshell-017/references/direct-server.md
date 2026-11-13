# OpenCode in a separate OpenShift project with Dev Spaces and MLflow

Follow this workflow with an authenticated `oc` session. All source files and commands needed are included here. When asked only to prepare files, generate them without applying to a cluster. Never report live deployment or trace export as verified until section 7 passes.

For disconnected or restricted-network targets, follow [the disconnected deployment skill](../../deploy-opencode-disconnected/SKILL.md) and [its runbook](../../../docs/disconnected-deployment.md). Build and publish the final image in connected CI, mirror its digest and the images in the workload, then deploy the prebuilt image. The source Containerfile downloads from public services, so do not run its BuildConfig in a disconnected cluster.

## 1. Establish the deployment contract

- Target OpenShift 4.20+; reuse an already configured OpenShift AI 3.5 MLflow instance. The cluster hosting RHOAI must independently satisfy its supported platform matrix.
- Deploy the server in `opencode-agents`; connect from the developer's actual Dev Spaces workspace namespace, such as `alice-devspaces`, not the operator namespace. Keep provider and MLflow credentials on the server. Provision only server-login credentials to Dev Spaces.
- Use one deployment/PVC/password per developer or trusted workspace group. Basic authentication is not per-user project isolation: authorized clients can operate the server's repositories and tools. Use separate deployments for mutually untrusted users.
- Run file access, tools, and tests on the server checkout. Remote attach does not synchronize Dev Spaces files, unsaved buffers, editor selection, or toolchains. Transfer reviewed changes through Git; include repository instructions and tests in the server checkout.
- Use one replica, `Recreate`, and a block-backed RWO PVC for workspace and SQLite/session state. Never share one state directory between server replicas.
- Run `opencode serve --hostname 0.0.0.0 --port 4096`; expose only `Service/opencode-server`, type `ClusterIP`. Use HTTP Basic authentication and namespace-scoped ingress policy. This Service is HTTP; add an approved internal TLS proxy/service mesh if encrypted client-to-server transport is required.
- Configure an OpenAI-compatible endpoint, such as vLLM, MaaS, or an API gateway. This template is not a native Anthropic/Vertex provider configuration.
- Inject provider settings from `Secret/opencode-provider` as environment variables; generate OpenCode configuration in process memory. Never put credentials in Git, ConfigMaps, build arguments, logs, or prompt text.
- Send traces directly to the existing MLflow HTTPS service. Use a dedicated ServiceAccount, workspace-scoped integration RoleBinding, and mounted service CA. Do not disable TLS verification or grant `edit`, `anyuid`, or `privileged` for this workflow.

Source baseline reviewed 2026-11-07: starter-kit commit `838546a258193667ed79845d70196c274bbff7f7`. Its MLflow Containerfile uses OpenCode image `quay.io/opendatahub/odh-opencode-rhel9:v1.18.3` and MLflow 3.14. Its base manifests use a different image tag; deliberately use the MLflow baseline here. Preserve this v1 configuration schema (`plugin`, singular); do not silently substitute OpenCode v2 or its current documentation schema.

Adapt the kit in three places: add real `serve` mode instead of its CLI `sleep infinity`; retain a direct internal Service instead of its OAuth/Route path; build the MLflow plugin into the image and refresh its bearer token from the mounted file per request. The last change is a small local SDK patch, not a stock OpenCode or MLflow feature. The kit's startup-only token export can expire during a long-running server process.

## 2. Discover inputs and prerequisites

Require `oc`, Git, Python 3, project workload/build permissions, block storage, and build access to Quay, the UBI registry, GitHub, npm, and PyPI for connected builds. A disconnected cluster uses the mirrored prebuilt image and does not run the public-fetching BuildConfig. Bootstrap requires permission to label the workspace namespace and provision its client Secret; normal clients need no Kubernetes access to the server project. Use amd64 for this baseline. Inspect existing same-name resources before applying; reuse only an intended deployment.

```bash
set -euo pipefail
oc whoami
oc get clusterversion version
oc get nodes -L kubernetes.io/arch
oc get storageclass
oc get mlflows.mlflow.opendatahub.io -A
oc get svc -A | grep -i mlflow
oc get clusterroles -o name | grep 'mlflow.*integration'
oc get devworkspaces.workspace.devfile.io -A
export NS=opencode-agents
export DEV_NS=alice-devspaces
export REPO_URL='https://git.example.com/team/app.git'
export REPO_BRANCH='feature/opencode-task'
export REMOTE_REPO_DIR=/data/workspace/app
export MLFLOW_NS=redhat-ods-applications
export MLFLOW_SERVICE=mlflow
export MLFLOW_PORT=8443
export MLFLOW_PATH=/mlflow
export MLFLOW_ROLE=mlflow-operator-mlflow-integration
export STORAGE_CLASS=gp3-csi
export BASE_URL='https://YOUR-MODEL-ENDPOINT/v1'
export PROVIDER_ID=rhoai
export MODEL_NAME='YOUR-SERVED-MODEL-ID'
export MODEL_CONTEXT=32768
export MODEL_OUTPUT=4096
export MLFLOW_TRACKING_URI="https://${MLFLOW_SERVICE}.${MLFLOW_NS}.svc:${MLFLOW_PORT}${MLFLOW_PATH}"
oc -n "$MLFLOW_NS" get svc "$MLFLOW_SERVICE" -o yaml
oc get clusterrole "$MLFLOW_ROLE" -o yaml
namespace_name=$(oc get namespace "$NS" --ignore-not-found -o name)
if [ -z "$namespace_name" ]; then oc new-project "$NS"; fi
oc -n "$NS" create serviceaccount opencode-server --dry-run=client -o yaml | oc apply -f -
oc -n "$NS" create rolebinding opencode-mlflow --clusterrole="$MLFLOW_ROLE" \
  --serviceaccount="$NS:opencode-server" --dry-run=client -o yaml | oc apply -f -
```

Replace examples with observed values. Service name, port, prefix, and integration ClusterRole vary; the alternate role name may be `mlflow-integration`. Inspect the MLflow CR and Service. If no MLflow instance/role exists, have its administrator enable/configure MLflow first; do not modify the shared DataScienceCluster or install a substitute automatically. `MLFLOW_WORKSPACE` below is the OpenCode namespace, not MLflow's installation namespace. For a different authorized workspace, place the RoleBinding there and set that workspace explicitly.

Set `BASE_URL` to the API root, normally ending in `/v1`, never `/chat/completions`. For MaaS, preserve the model path prefix and append `/v1` only if its API requires it. Use the served model ID from `/models`, including embedded slashes. Confirm tool calling and context/output budgets; example limits are not model capability claims. Keep output comfortably below context.

## 3. Create Secret-backed environment settings

Run from a terminal with secure input. Update the provider Secret and generate a server password only when its Secret is absent. For an endpoint without authentication, enter its required dummy key. Do not print Secret values during diagnostics.

```bash
python3 - <<'PY'
import os, json, base64, getpass, secrets, subprocess
ns = os.environ['NS']
def save(name, values, create=False):
    doc = {'apiVersion':'v1','kind':'Secret','metadata':{'name':name,'namespace':ns},
           'type':'Opaque','data':{k:base64.b64encode(v.encode()).decode() for k,v in values.items()}}
    command = ['oc','create','-f','-'] if create else ['oc','apply','--server-side','--field-manager=opencode-setup','-f','-']
    subprocess.run(command, input=json.dumps(doc), text=True, check=True)
values = {k:os.environ[k] for k in ['BASE_URL','PROVIDER_ID','MODEL_NAME','MODEL_CONTEXT','MODEL_OUTPUT']}
values['API_KEY'] = getpass.getpass('Model API key (hidden): ')
if not values['API_KEY']: raise SystemExit('API key must not be empty')
save('opencode-provider', values)
existing = subprocess.run(['oc','-n',ns,'get','secret','opencode-server-auth',
                           '--ignore-not-found','-o','name'],
                          stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
if not existing:
    save('opencode-server-auth', {'OPENCODE_SERVER_USERNAME':'opencode',
                                'OPENCODE_SERVER_PASSWORD':secrets.token_urlsafe(32)}, create=True)
PY
```

## 4. Build the server image

Create a build directory. Follow the kit's MLflow approach, use Node 22 to build the SDK (requires Node >=18), include plugin dependencies at build time, and pin Python SDK/plugin source to 3.14.0. Keep the resulting image digest. In controlled CI, also pin the Node builder image to an approved digest and mirror dependencies. The OpenCode base digest below was resolved from Quay during preparation. This build section is for connected CI; on the disconnected cluster, deploy the mirrored prebuilt image and skip the source build.

```bash
mkdir -p opencode-server-build
cd opencode-server-build
cat > patch-mlflow.cjs <<'JS'
const fs = require('node:fs');
const p = '/opt/mlflow-src/libs/typescript/core/src/auth/index.ts';
let s = fs.readFileSync(p, 'utf8');
const anchor = "    if (workspace) {\n      headers['X-MLFLOW-WORKSPACE'] = workspace;\n    }";
if (s.split(anchor).length !== 2) throw new Error('MLflow auth source changed; review patch');
const patch = `    // Local integration: reread the rotating projected token per request.
    if (process.env.MLFLOW_TOKEN_PATH) {
      const { readFile } = await import('node:fs/promises');
      const token = (await readFile(process.env.MLFLOW_TOKEN_PATH, 'utf8')).trim();
      if (!token) throw new Error('Empty MLflow service-account token');
      headers['Authorization'] = 'Bearer ' + token;
    }
`;
fs.writeFileSync(p, s.replace(anchor, patch + anchor));
JS
cat > Containerfile <<'DOCKER'
FROM registry.access.redhat.com/ubi9/nodejs-22:latest AS plugin
USER 0
RUN git clone --depth 1 --branch v3.14.0 https://github.com/mlflow/mlflow.git /opt/mlflow-src
COPY patch-mlflow.cjs /tmp/patch-mlflow.cjs
RUN node /tmp/patch-mlflow.cjs
WORKDIR /opt/mlflow-src/libs/typescript
RUN npm ci --ignore-scripts \
 && npm run build --workspace=@mlflow/core \
 && npm run build --workspace=@mlflow/opencode \
 && npm prune --omit=dev --ignore-scripts
FROM quay.io/opendatahub/odh-opencode-rhel9:v1.18.3@sha256:532b2b83e7762d703fb50b26f54b34a73e5faf52432b92e19e3bd4531c06e9ea
USER 0
RUN pip3 install --no-cache-dir 'mlflow[kubernetes]==3.14.0'
COPY --from=plugin /opt/mlflow-src/libs/typescript /opt/mlflow-ts
COPY start.py health.py /opt/opencode-launch/
RUN chmod -R a+rX /opt/mlflow-ts /opt/opencode-launch
USER 1001
ENTRYPOINT ["python3", "/opt/opencode-launch/start.py"]
DOCKER
cat > start.py <<'PY'
import json, os, pathlib, subprocess
os.umask(0o077)
e = os.environ
for k in ['BASE_URL','API_KEY','PROVIDER_ID','MODEL_NAME','OPENCODE_SERVER_PASSWORD',
          'MLFLOW_TRACKING_URI','MLFLOW_WORKSPACE','MLFLOW_EXPERIMENT_NAME']:
    if not e.get(k): raise RuntimeError('Missing environment setting: '+k)
for k in ['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME']:
    pathlib.Path(e[k]).mkdir(parents=True, exist_ok=True)
workspace = pathlib.Path('/data/workspace')
workspace.mkdir(exist_ok=True)
os.chdir(workspace)
for key, value in [('user.name','OpenCode'),('user.email','opencode@openshift.local'),('init.defaultBranch','main')]:
    subprocess.run(['git','config','--global','--replace-all',key,value], check=True)
subprocess.run(['git','config','--global','--replace-all','safe.directory',str(workspace),r'^/data/workspace$'], check=True)
if not (workspace/'.git').exists(): subprocess.run(['git','init','-q'], check=True)
pid, model = e['PROVIDER_ID'], e['MODEL_NAME']
context, output = int(e['MODEL_CONTEXT']), int(e['MODEL_OUTPUT'])
if not (0 < output < context): raise RuntimeError('Require 0 < MODEL_OUTPUT < MODEL_CONTEXT')
config = {'$schema':'https://opencode.ai/config.json', 'autoupdate':False,
          'provider':{pid:{'npm':'@ai-sdk/openai-compatible','name':pid,
            'options':{'baseURL':e['BASE_URL'],'apiKey':e['API_KEY']},
            'models':{model:{'name':model,'limit':{'context':context,'output':output}}}}},
          'model':pid+'/'+model, 'small_model':pid+'/'+model, 'enabled_providers':[pid],
          'plugin':['file:///opt/mlflow-ts/integrations/opencode']}
e['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
# Python uses Kubernetes auth; the patched TypeScript SDK uses MLFLOW_TOKEN_PATH.
# Do not snapshot the rotating token into MLFLOW_TRACKING_TOKEN.
e.pop('MLFLOW_TRACKING_TOKEN', None)
import mlflow
mlflow.set_tracking_uri(e['MLFLOW_TRACKING_URI'])
experiment = mlflow.set_experiment(e['MLFLOW_EXPERIMENT_NAME'])
e['MLFLOW_EXPERIMENT_ID'] = str(experiment.experiment_id)
pathlib.Path('/tmp/mlflow-experiment-id').write_text(e['MLFLOW_EXPERIMENT_ID'])
print('MLflow experiment ready: '+e['MLFLOW_EXPERIMENT_ID'], flush=True)
os.execvp('opencode', ['opencode','serve','--hostname','0.0.0.0','--port','4096'])
PY
cat > health.py <<'PY'
import base64, json, os, urllib.request
creds = os.environ['OPENCODE_SERVER_USERNAME']+':'+os.environ['OPENCODE_SERVER_PASSWORD']
req = urllib.request.Request('http://127.0.0.1:4096/global/health',
       headers={'Authorization':'Basic '+base64.b64encode(creds.encode()).decode()})
with urllib.request.urlopen(req, timeout=3) as r:
    assert r.status == 200 and json.load(r).get('healthy') is True
PY
cat > build.yaml <<EOF
apiVersion: image.openshift.io/v1
kind: ImageStream
metadata: {name: opencode-server, namespace: ${NS}}
---
apiVersion: build.openshift.io/v1
kind: BuildConfig
metadata: {name: opencode-server, namespace: ${NS}}
spec:
  runPolicy: Serial
  nodeSelector: {kubernetes.io/arch: amd64}
  source: {type: Binary}
  strategy:
    type: Docker
    dockerStrategy: {dockerfilePath: Containerfile}
  output: {to: {kind: ImageStreamTag, name: 'opencode-server:validated'}}
  resources:
    requests: {cpu: '1', memory: 2Gi}
    limits: {cpu: '2', memory: 4Gi}
EOF
oc apply -f build.yaml
oc -n "$NS" start-build opencode-server --from-dir=. --follow --wait
OPENCODE_IMAGE=$(oc -n "$NS" get istag opencode-server:validated -o jsonpath='{.image.dockerImageReference}')
export OPENCODE_IMAGE
test -n "$OPENCODE_IMAGE"
```

Without binary Docker builds/internal registry, build the same directory with Podman in approved CI, push to an approved registry, set `OPENCODE_IMAGE` to that digest, and configure pull credentials. Do not change workload SCC to solve build-policy restrictions. Verify that the local plugin resolves the patched workspace `@mlflow/core`, not a second registry copy, in section 7.

## 5. Apply the internal Service and workload

Use the shell variables from section 2. Do not also apply the kit's base Kustomization: it creates an OAuth proxy and Route. Use a dedicated CA ConfigMap because injection replaces its data. Mount the directory, not a `subPath`, so CA updates propagate; restart after trust changes.

```bash
cat > workload.yaml <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: opencode-service-ca
  namespace: ${NS}
  annotations: {service.beta.openshift.io/inject-cabundle: 'true'}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: opencode-data, namespace: ${NS}}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: ${STORAGE_CLASS}
  resources: {requests: {storage: 10Gi}}
---
apiVersion: v1
kind: Service
metadata: {name: opencode-server, namespace: ${NS}}
spec:
  type: ClusterIP
  selector: {app: opencode-server}
  ports: [{name: http, port: 4096, targetPort: http}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: opencode-server, namespace: ${NS}}
spec:
  replicas: 1
  strategy: {type: Recreate}
  selector: {matchLabels: {app: opencode-server}}
  template:
    metadata: {labels: {app: opencode-server}}
    spec:
      nodeSelector: {kubernetes.io/arch: amd64}
      serviceAccountName: opencode-server
      automountServiceAccountToken: true
      terminationGracePeriodSeconds: 60
      securityContext: {runAsNonRoot: true, seccompProfile: {type: RuntimeDefault}}
      containers:
      - name: opencode
        image: ${OPENCODE_IMAGE}
        imagePullPolicy: IfNotPresent
        ports: [{name: http, containerPort: 4096}]
        envFrom:
        - secretRef: {name: opencode-provider}
        - secretRef: {name: opencode-server-auth}
        env:
        - {name: HOME, value: /data/home}
        - {name: XDG_CONFIG_HOME, value: /data/config}
        - {name: XDG_DATA_HOME, value: /data/share}
        - {name: XDG_STATE_HOME, value: /data/state}
        - {name: XDG_CACHE_HOME, value: /data/cache}
        - {name: MLFLOW_TRACKING_URI, value: '${MLFLOW_TRACKING_URI}'}
        - {name: MLFLOW_WORKSPACE, value: '${NS}'}
        - {name: MLFLOW_EXPERIMENT_NAME, value: opencode-traces}
        - {name: MLFLOW_TRACKING_AUTH, value: kubernetes-namespaced}
        - {name: MLFLOW_TOKEN_PATH, value: /var/run/secrets/kubernetes.io/serviceaccount/token}
        - {name: MLFLOW_TRACKING_SERVER_CERT_PATH, value: /etc/mlflow-ca/service-ca.crt}
        - {name: NODE_EXTRA_CA_CERTS, value: /etc/mlflow-ca/service-ca.crt}
        resources:
          requests: {cpu: 500m, memory: 1Gi}
          limits: {cpu: '2', memory: 4Gi}
        securityContext:
          allowPrivilegeEscalation: false
          capabilities: {drop: [ALL]}
        startupProbe:
          exec: {command: [python3, /opt/opencode-launch/health.py]}
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 60
        readinessProbe:
          exec: {command: [python3, /opt/opencode-launch/health.py]}
          periodSeconds: 10
          timeoutSeconds: 5
        livenessProbe:
          exec: {command: [python3, /opt/opencode-launch/health.py]}
          periodSeconds: 30
          timeoutSeconds: 5
          failureThreshold: 3
        volumeMounts:
        - {name: data, mountPath: /data}
        - {name: ca, mountPath: /etc/mlflow-ca, readOnly: true}
      volumes:
      - {name: data, persistentVolumeClaim: {claimName: opencode-data}}
      - name: ca
        configMap:
          name: opencode-service-ca
          items: [{key: service-ca.crt, path: service-ca.crt}]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: opencode-server-ingress, namespace: ${NS}}
spec:
  podSelector: {matchLabels: {app: opencode-server}}
  policyTypes: [Ingress]
  ingress:
  - from:
    - podSelector: {}
    - namespaceSelector: {matchLabels: {opencode-client: 'true'}}
    ports: [{protocol: TCP, port: 4096}]
EOF
oc apply --dry-run=server -f workload.yaml
oc apply -f workload.yaml
oc -n "$NS" rollout status deployment/opencode-server --timeout=10m
```

Let SCC admission assign the UID and filesystem group; require storage that honors that group. This policy does not restrict egress: existing cluster/namespace policies must allow DNS, model, and MLflow traffic. MLflow ingress must admit this namespace. No GPU/model workload is created; resource requests cover only the coding-agent server.

## 6. Connect Dev Spaces to the separate server project

From the bootstrap/admin terminal, use the variables from section 2. Verify `DEV_NS` contains the intended developer workspace. Save workspace work first: applying or changing an auto-mounted Secret can restart running workspaces in that namespace. These labels inject credentials into all its workspace containers; use this only for an authorized user/group namespace.

```bash
test "$NS" != "$DEV_NS"
oc -n "$DEV_NS" get devworkspaces.workspace.devfile.io
oc label namespace "$DEV_NS" opencode-client=true --overwrite
oc -n "$DEV_NS" get networkpolicy
python3 - <<'PY'
import os, json, subprocess
source = json.loads(subprocess.check_output(['oc','-n',os.environ['NS'],'get','secret','opencode-server-auth','-o','json']))
keys = ['OPENCODE_SERVER_USERNAME','OPENCODE_SERVER_PASSWORD']
if not all(source.get('data',{}).get(k) for k in keys): raise SystemExit('Server login Secret is incomplete')
doc = {'apiVersion':'v1','kind':'Secret','type':'Opaque',
       'metadata':{'name':'opencode-remote-auth','namespace':os.environ['DEV_NS'],
         'labels':{'controller.devfile.io/mount-to-devworkspace':'true','controller.devfile.io/watch-secret':'true'},
         'annotations':{'controller.devfile.io/mount-as':'env'}},
       'data':{k:source['data'][k] for k in keys}}
subprocess.run(['oc','apply','--server-side','--field-manager=opencode-devspaces','-f','-'],
               input=json.dumps(doc), text=True, check=True)
PY
```

This copies only the two server-login keys, with fresh metadata; never copy `opencode-provider` or the ServiceAccount token. Re-run on password rotation, then restart/verify the workspace. Secrets and PVC claims cannot be referenced across namespaces. This design uses independent PVCs and Git, without mounting the Dev Spaces PVC into the server.

The server ingress policy admits all pods in labeled namespaces. If source egress is restricted, add an allow rule to the existing design for destination namespace `kubernetes.io/metadata.name: opencode-agents` AND pod label `app: opencode-server`, TCP 4096, plus cluster DNS UDP/TCP 53. Substitute the actual server namespace. Do not replace other egress rules; NetworkPolicies are additive, and both source egress and destination ingress must allow the connection. Also check cluster-wide network policy.

Prepare the server's repository from the admin terminal. Set `REPO_BRANCH` to an existing approved remote work branch; commit/push the Dev Spaces baseline first when authorized. For private Git, provision a dedicated credential-helper/SSH Secret in `NS` with repository-limited permissions and verified host keys/CA before cloning; Dev Spaces Git credentials do not propagate. Never embed a token in `REPO_URL`, disable host verification, or forward all workspace credentials. Add the project's compilers, test tools, and dependency trust to the server image as required.

```bash
oc -n "$NS" exec deployment/opencode-server -c opencode -- \
  env GIT_TERMINAL_PROMPT=0 git clone --single-branch --branch "$REPO_BRANCH" -- "$REPO_URL" "$REMOTE_REPO_DIR"
oc -n "$NS" exec deployment/opencode-server -c opencode -- git config --global --add safe.directory "$REMOTE_REPO_DIR"
oc -n "$NS" exec deployment/opencode-server -c opencode -- git -C "$REMOTE_REPO_DIR" rev-parse HEAD
```

Clone only into a new path. For an existing checkout, inspect its remote, branch, and changes; never delete/reset it to make this command succeed. Compare its commit to `git rev-parse HEAD` in Dev Spaces. Keep `AGENTS.md`, architecture notes, build/test commands, and conventions in the repository; use `/init` in the attached session if instructions are missing, then review the generated file.

In the **Dev Spaces workspace terminal**, install the matching CLI (or bake it into the workspace image), and attach. Server-login variables arrive from `opencode-remote-auth`; check presence without printing them. Change the DNS suffix if the cluster uses a nonstandard cluster domain.

```bash
npm install --global --prefix "$HOME/.local" opencode-ai@1.18.3
export PATH="$HOME/.local/bin:$PATH"
export OPENCODE_URL='http://opencode-server.opencode-agents.svc.cluster.local:4096'
export REMOTE_REPO_DIR=/data/workspace/app
test -n "${OPENCODE_SERVER_USERNAME:-}" && test -n "${OPENCODE_SERVER_PASSWORD:-}"
opencode --version
opencode attach "$OPENCODE_URL" --dir "$REMOTE_REPO_DIR"
```

`--dir` selects the **server-side** path. Normal `opencode` without `attach` starts a local agent. Use the remote TUI here; do not assume the VS Code extension automatically targets this server or forwards current editor selection. Reference server-visible files explicitly in prompts, for example: “Read AGENTS.md, src/api and tests; add pagination using the existing patterns, run the documented tests, and show the diff. Do not commit or push.”

For each task: save/commit/push approved input changes in Dev Spaces; pause the agent, require a clean server checkout on the intended branch, then update it. Run this from the admin terminal with section 2 variables:

```bash
oc -n "$NS" exec deployment/opencode-server -c opencode -- env GIT_TERMINAL_PROMPT=0 sh -ec '
  cd "$1"
  test -z "$(git status --porcelain)"
  test "$(git branch --show-current)" = "$2"
  git fetch origin "$2"
  git merge --ff-only FETCH_HEAD
  test "$(git rev-parse HEAD)" = "$(git rev-parse FETCH_HEAD)"
' sh "$REMOTE_REPO_DIR" "$REPO_BRANCH"
```

Require the server commit to match the approved Dev Spaces baseline; a failed refresh may indicate unpushed commits or divergence. After generation, review the server diff and test results. Commit only selected files to the task branch and push only when authorized; fetch/fast-forward that branch in Dev Spaces with a clean checkout. Resolve divergence normally, without force/reset. Uncommitted and unsaved Dev Spaces edits are absent until explicitly transferred. For immediate access to live workspace files, run the agent in the workspace instead; this remote design does not provide live synchronization.

For local administration, `oc -n "$NS" port-forward svc/opencode-server 4096:4096` exposes localhost only; use the same authentication. `/doc` exposes the server schema. No external Route or browser UI is provisioned.

## 7. Verify Service access, inference, and matching MLflow traces

Inspect events, image version, SCC, PVC, Service, and startup logs. Do not dump `/config`, process environments, or Secret data: they can contain credentials.

```bash
oc -n "$NS" get pods,svc,pvc
oc -n "$NS" get pod -l app=opencode-server -o jsonpath='{range .items[*]}{.metadata.name}{" SCC="}{.metadata.annotations.openshift\.io/scc}{"\n"}{end}'
oc -n "$NS" logs deployment/opencode-server -c opencode --tail=80
oc -n "$NS" exec deployment/opencode-server -c opencode -- opencode --version
oc -n "$NS" exec deployment/opencode-server -c opencode -- \
  sh -c 'test "$(readlink -f /opt/mlflow-ts/node_modules/@mlflow/core)" = /opt/mlflow-ts/core && grep -q MLFLOW_TOKEN_PATH /opt/mlflow-ts/core/dist/auth/index.js'
```

Run the next Python check **inside Dev Spaces** with `OPENCODE_URL`, `REMOTE_REPO_DIR`, and the mounted login variables from section 6. It proves cross-namespace Service access, authentication, the selected server directory, and inference. Python 3 is required in the client container; no model key or Kubernetes token is used by this check.

```bash
python3 - <<'PY'
import os, json, base64, urllib.request, urllib.error, urllib.parse, time
base = os.environ['OPENCODE_URL'].rstrip('/')
directory = os.environ['REMOTE_REPO_DIR']
query = '?'+urllib.parse.urlencode({'directory':directory})
creds = os.environ['OPENCODE_SERVER_USERNAME']+':'+os.environ['OPENCODE_SERVER_PASSWORD']
auth = 'Basic '+base64.b64encode(creds.encode()).decode()
def api(path, data=None):
    req = urllib.request.Request(base+path+query, data=None if data is None else json.dumps(data).encode(),
          headers={'Authorization':auth,'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=240) as r: return json.load(r)
assert api('/global/health')['healthy']
assert api('/path')['directory'] == directory, 'Wrong server-side repository directory'
try:
    urllib.request.urlopen(base+'/session'+query, timeout=10)
    raise RuntimeError('Unauthenticated session access unexpectedly succeeded')
except urllib.error.HTTPError as exc:
    assert exc.code == 401, exc.code
marker = 'opencode-smoke-'+str(int(time.time()))
session = api('/session', {'title':marker})
response = api('/session/'+session['id']+'/message',
               {'parts':[{'type':'text','text':'Do not use tools. Reply exactly: '+marker}]})
text = '\n'.join(p.get('text','') for p in response.get('parts',[]) if p.get('type')=='text')
assert marker in text, 'Expected model response marker was not returned'
print(json.dumps({'session_id':session['id'],'marker':marker,'reply':text}), flush=True)
PY
```

Require exit status 0 and the matching reply. From the **admin terminal**, set the exact printed marker and verify MLflow using the server's identity/workspace (`NS`, normally `opencode-agents`). An experiment alone does not prove trace export. Keep the server running so the plugin flushes on `session.idle`.

```bash
export SMOKE_MARKER='PASTE-THE-PRINTED-MARKER'
oc -n "$NS" exec -i deployment/opencode-server -c opencode -- \
  env SMOKE_MARKER="$SMOKE_MARKER" python3 - <<'PY'
import os, pathlib, time, mlflow
id = pathlib.Path('/tmp/mlflow-experiment-id').read_text().strip()
for attempt in range(12):
    traces = mlflow.search_traces(experiment_ids=[id], max_results=50, return_type='list')
    found = [t for t in traces if os.environ['SMOKE_MARKER'] in str(t.to_dict())]
    if found:
        print('Verified OpenCode trace:', found[0].info.trace_id)
        break
    time.sleep(5)
else:
    raise SystemExit('No matching trace; inspect plugin/auth/TLS errors')
PY
oc get consolelink mlflow -o jsonpath='{.spec.href}{"\n"}'
```

Open the returned UI link, select workspace `${NS}` and experiment `opencode-traces`, and inspect the matching prompt/response, model, tokens, and spans. Traces may contain prompts, code, and tool output; apply the project's data policy. RHOAI 3.5 documentation lists server 3.13.0 and Python SDK >=3.11. This kit-derived client is 3.14.0; a matching trace is the compatibility acceptance gate.

Test context with a benign unique marker in a committed repository file, synchronize Git, then ask the attached agent to read that file and report the marker. Test a small code change: confirm its diff appears on the server and reaches Dev Spaces only after the reviewed Git transfer. This tests code context separately from inference.

During a planned verification window, restart with `oc -n "$NS" rollout restart deployment/opencode-server`, await rollout, and read the previous session through authenticated `GET /session/<id>?directory=<URL-encoded-server-path>`. Verify repository files and session history persist. Keep the server running through projected-token rotation and send another marked prompt; require a fresh trace. Inspect expiry metadata only, never log tokens.

## 8. Operate and troubleshoot

| Symptom | Action |
|---|---|
| Model 401/404 or unknown model | Verify Secret keys, API root, served model ID, and `provider/model` selection. Test from a pod without logging the key. |
| Prompt hangs or tool call fails | Verify tool calling, context/output budget, and session errors/permission requests. |
| MLflow 401 after hours | Verify token rotation, patched core resolution, and `MLFLOW_TOKEN_PATH`. Do not add a long-lived token Secret. |
| MLflow 403 | Check the integration RoleBinding in the workspace, SA subject, actual ClusterRole, and `MLFLOW_WORKSPACE`. With impersonation rights: `oc auth can-i create experiments.mlflow.kubeflow.org --as=system:serviceaccount:$NS:opencode-server -n "$NS"`. |
| MLflow 404 | Check service port/path. Do not append `/v1` or `/v1/traces` to the tracking URI. |
| TLS error | Check injected CA, Service DNS certificate name, Python certificate path, and `NODE_EXTRA_CA_CERTS`. Restart after trust changes; never disable verification. |
| Experiment exists, no trace | Check plugin loading, idle event, experiment ID in the server process, and protocol compatibility. Temporarily enable `MLFLOW_OPENCODE_DEBUG=true` in a controlled test; logs may contain sensitive output. |
| PVC/SQLite permissions | Inspect storage events, admitted SCC/fsGroup, and parent-directory write access under the assigned UID. Use block storage; never delete the database or grant privileged SCC to fix this. |
| Service unreachable | Check EndpointSlices, readiness, namespace labels, additive NetworkPolicies, and DNS. Local health alone does not prove Service access. |
| Dev Spaces login fails | Verify the two login variables are present without printing them, resync `opencode-remote-auth`, and restart the workspace after rotation. |
| Agent sees stale/wrong code | Check remote `--dir`, branch and commit; compare both checkouts. Editor buffers and Git credentials are not forwarded by attach. |

After a server Secret change, restart the Deployment: existing process environments do not update. Rotate model and client credentials separately; for server-password changes also resync the client Secret and restart Dev Spaces. Rebuild/retest upgrades and record previous image digests/manifests. `oc rollout undo deployment/opencode-server -n "$NS"` restores a pod template, not Secret values or database format; restore compatible values/backups as needed.

For teardown, delete only this deployment's named Deployment, Service, NetworkPolicy, RoleBinding, ServiceAccount, CA ConfigMap, and optionally build/ImageStream. Retain `opencode-data`, both server Secrets and the client Secret unless deletion is requested. Remove a client namespace label only if no other server needs it. Never delete shared MLflow, Dev Spaces resources, or an existing namespace as routine cleanup.

## Sources

- [Pinned starter-kit source](https://github.com/red-hat-data-services/agentic-starter-kits/tree/838546a258193667ed79845d70196c274bbff7f7/agents/opencode), especially `deployment/Containerfile.mlflow` and `deployment/overlays/mlflow-tracing/entrypoint-patch.yaml`.
- [Kit MLflow authentication/TLS](https://github.com/red-hat-data-services/agentic-starter-kits/blob/838546a258193667ed79845d70196c274bbff7f7/docs/mlflow-openshift-auth-and-tls.md).
- [MLflow 3.14.0 auth](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/core/src/auth/index.ts) and [OpenCode plugin](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/integrations/opencode/src/index.ts).
- [OpenCode server API](https://docs.opencode.ai/docs/server/) and [v1.18.3 plugin loader](https://github.com/anomalyco/opencode/blob/v1.18.3/packages/opencode/src/plugin/index.ts). Prefer the pinned server's `/doc` when current docs differ.
- [RHOAI 3.5 MLflow/RBAC](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-mlflow_mlflow) and [SDK/authentication/compatibility](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-and-authenticating-mlflow-sdk_mlflow).
- [MLflow OpenCode tracing](https://mlflow.org/docs/latest/genai/tracing/integrations/listing/opencode/) and [OCP 4.20 service CA](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/security_and_compliance/configuring-certificates).
- [OpenCode v1.18.3 remote attach](https://github.com/anomalyco/opencode/blob/v1.18.3/packages/opencode/src/cli/cmd/attach.ts) and [repository instructions](https://docs.opencode.ai/docs/rules/).
- [Dev Spaces workspace Secrets](https://docs.redhat.com/en/documentation/red_hat_openshift_dev_spaces/3.28/html/user_guide/assembly_credentials-and-configurations_user_guide); verify behavior against the installed Dev Spaces version.
- Kubernetes [cross-namespace DNS](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/), [NetworkPolicies](https://kubernetes.io/docs/concepts/services-networking/network-policies/), and [namespace-scoped PVCs](https://kubernetes.io/docs/concepts/storage/persistent-volumes/).
