---
name: deploy-opencode-openshift-mlflow
description: Deploy a persistent OpenCode HTTP server on OpenShift 4.20 or later with an internal ClusterIP Service, OpenAI-compatible provider settings injected from an OpenShift Secret, and traces sent to the existing OpenShift AI 3.5 MLflow service. Use for standalone OpenCode server deployments, MaaS or vLLM configuration, MLflow workspace RBAC and TLS, token rotation, verification, upgrades, and troubleshooting. Do not install OpenShell or deploy another MLflow server.
---

# OpenCode server on OpenShift with MLflow

For disconnected or restricted-network targets, read [../../docs/disconnected-deployment.md](../../docs/disconnected-deployment.md) before building. The current Containerfile fetches public sources and packages; mirror them or promote a prebuilt digest-pinned image.

Follow this workflow with an authenticated `oc` session. All source files and commands needed are included here. When asked only to prepare files, generate them without applying to a cluster. Never report live deployment or trace export as verified until section 7 passes.

## 1. Establish the deployment contract

- Target OpenShift 4.20+; reuse an already configured OpenShift AI 3.5 MLflow instance. The cluster hosting RHOAI must independently satisfy its supported platform matrix.
- Use one replica, `Recreate`, and a block-backed RWO PVC for workspace and SQLite/session state. Never share one state directory between server replicas.
- Run `opencode serve --hostname 0.0.0.0 --port 4096`; expose only `Service/opencode-server`, type `ClusterIP`. Use HTTP Basic authentication and namespace-scoped ingress policy. This Service is HTTP; add an approved internal TLS proxy/service mesh if encrypted client-to-server transport is required.
- Configure an OpenAI-compatible endpoint, such as vLLM, MaaS, or an API gateway. This template is not a native Anthropic/Vertex provider configuration.
- Inject provider settings from `Secret/opencode-provider` as environment variables; generate OpenCode configuration in process memory. Never put credentials in Git, ConfigMaps, build arguments, logs, or prompt text.
- Send traces directly to the existing MLflow HTTPS service. Use a dedicated ServiceAccount, workspace-scoped integration RoleBinding, and mounted service CA. Do not disable TLS verification or grant `edit`, `anyuid`, or `privileged` for this workflow.

Source baseline reviewed 2026-11-07: starter-kit commit `838546a258193667ed79845d70196c274bbff7f7`. Its MLflow Containerfile uses OpenCode image `quay.io/opendatahub/odh-opencode-rhel9:v1.18.3` and MLflow 3.14. Its base manifests use a different image tag; deliberately use the MLflow baseline here. Preserve this v1 configuration schema (`plugin`, singular); do not silently substitute OpenCode v2 or its current documentation schema.

Adapt the kit in three places: add real `serve` mode instead of its CLI `sleep infinity`; retain a direct internal Service instead of its OAuth/Route path; build the MLflow plugin into the image and refresh its bearer token from the mounted file per request. The last change is a small local SDK patch, not a stock OpenCode or MLflow feature. The kit's startup-only token export can expire during a long-running server process.

## 2. Discover inputs and prerequisites

Require `oc`, Git, Python 3, project workload/build permissions, block storage, and build access to Quay, the UBI registry, GitHub, npm, and PyPI. Use amd64 for this baseline; verify image architecture before another target. Inspect existing same-name resources before applying; reuse only an intended deployment.

```bash
set -euo pipefail
oc whoami
oc get clusterversion version
oc get nodes -L kubernetes.io/arch
oc get storageclass
oc get mlflows.mlflow.opendatahub.io -A
oc get svc -A | grep -i mlflow
oc get clusterroles -o name | grep 'mlflow.*integration'
export NS=opencode
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
oc get namespace "$NS" >/dev/null 2>&1 || oc new-project "$NS"
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

Create a build directory. Follow the kit's MLflow approach, use Node 22 to build the SDK (requires Node >=18), include plugin dependencies at build time, and pin Python SDK/plugin source to 3.14.0. Keep the resulting image digest. In controlled CI, also pin the Node builder image to an approved digest and mirror dependencies. The OpenCode base digest below was resolved from Quay during preparation.

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
for key, value in [('user.name','OpenCode'),('user.email','opencode@openshift.local'),
                   ('init.defaultBranch','main'),('safe.directory',str(workspace))]:
    subprocess.run(['git','config','--global','--replace-all',key,value], check=True)
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

## 6. Connect internally

Use `http://opencode-server.${NS}.svc.cluster.local:4096` inside the cluster. When requested, allow another namespace with `oc label namespace CLIENT_NAMESPACE opencode-client=true --overwrite`; all its pods then pass this policy. Other policies are additive. Provision Basic-auth credentials only to authorized clients via their own Secret or a secret-management controller; Kubernetes cannot reference a Secret across namespaces. Do not reuse the model API key as the server password.

For local access, run `oc -n "$NS" port-forward svc/opencode-server 4096:4096`, then use `http://127.0.0.1:4096`. Use an OpenCode client compatible with the pinned server; inspect `opencode attach --help` for credential flags/environment support. API clients send `Authorization: Basic <base64(username:password)>`. This is a headless API, not a promised browser UI. `/doc` exposes its schema. No Route, NodePort, or LoadBalancer is created.

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

Use a separate disposable Pod to prove Service DNS, endpoints, ingress, authentication, and inference. Give it only server credentials, without a model key or Kubernetes token. A unique prompt marker links the response to its trace. Remove any previous `opencode-client-check` Pod before rerunning this check.

```bash
cat > client.py <<'PY'
import os, json, base64, urllib.request, urllib.error, time
base = 'http://opencode-server:4096'
creds = os.environ['OPENCODE_SERVER_USERNAME']+':'+os.environ['OPENCODE_SERVER_PASSWORD']
auth = 'Basic '+base64.b64encode(creds.encode()).decode()
def api(path, data=None):
    req = urllib.request.Request(base+path, data=None if data is None else json.dumps(data).encode(),
          headers={'Authorization':auth,'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=240) as r: return json.load(r)
assert api('/global/health')['healthy']
try:
    urllib.request.urlopen(base+'/session', timeout=10)
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
oc -n "$NS" create configmap opencode-client-check --from-file=client.py --dry-run=client -o yaml | oc apply -f -
cat > client.yaml <<EOF
apiVersion: v1
kind: Pod
metadata: {name: opencode-client-check, namespace: ${NS}}
spec:
  restartPolicy: Never
  nodeSelector: {kubernetes.io/arch: amd64}
  automountServiceAccountToken: false
  securityContext: {runAsNonRoot: true, seccompProfile: {type: RuntimeDefault}}
  containers:
  - name: check
    image: ${OPENCODE_IMAGE}
    command: [python3, /check/client.py]
    resources: {requests: {cpu: 100m, memory: 128Mi}, limits: {cpu: 500m, memory: 256Mi}}
    envFrom: [{secretRef: {name: opencode-server-auth}}]
    securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
    volumeMounts: [{name: check, mountPath: /check}]
  volumes: [{name: check, configMap: {name: opencode-client-check}}]
EOF
oc -n "$NS" create -f client.yaml
oc -n "$NS" logs -f pod/opencode-client-check --pod-running-timeout=120s
oc -n "$NS" get pod opencode-client-check -o jsonpath='{.status.phase}{"\n"}'
```

Require `Succeeded`. Then set the exact printed marker and verify MLflow using the server's identity/workspace. An experiment alone does not prove trace export. Keep the server running so the plugin flushes on `session.idle`.

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

During a planned verification window, restart with `oc -n "$NS" rollout restart deployment/opencode-server`, await rollout, and read the previous session through authenticated `GET /session/<id>` from the client. Verify workspace files/session history persist. Keep the server running through projected-token rotation and send another marked prompt; require a fresh trace. Inspect expiry metadata only, never log tokens. Remove the disposable check Pod and ConfigMap after collecting results.

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

After a Secret change, restart the Deployment: existing process environments do not update. Rotate model and client credentials separately. Rebuild/retest upgrades and record previous image digests/manifests. `oc rollout undo deployment/opencode-server -n "$NS"` restores a pod template, not Secret values or database format; restore compatible values/backups as needed.

For teardown, delete only this deployment's named Deployment, Service, NetworkPolicy, check resources, RoleBinding, ServiceAccount, CA ConfigMap, and optionally build/ImageStream. Retain `opencode-data` and both Secrets unless deletion is explicitly requested. Never delete shared MLflow or an existing namespace as routine cleanup.

## Sources

- [Pinned starter-kit source](https://github.com/red-hat-data-services/agentic-starter-kits/tree/838546a258193667ed79845d70196c274bbff7f7/agents/opencode), especially `deployment/Containerfile.mlflow` and `deployment/overlays/mlflow-tracing/entrypoint-patch.yaml`.
- [Kit MLflow authentication/TLS](https://github.com/red-hat-data-services/agentic-starter-kits/blob/838546a258193667ed79845d70196c274bbff7f7/docs/mlflow-openshift-auth-and-tls.md).
- [MLflow 3.14.0 auth](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/core/src/auth/index.ts) and [OpenCode plugin](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/integrations/opencode/src/index.ts).
- [OpenCode server API](https://docs.opencode.ai/docs/server/) and [v1.18.3 plugin loader](https://github.com/anomalyco/opencode/blob/v1.18.3/packages/opencode/src/plugin/index.ts). Prefer the pinned server's `/doc` when current docs differ.
- [RHOAI 3.5 MLflow/RBAC](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-mlflow_mlflow) and [SDK/authentication/compatibility](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-and-authenticating-mlflow-sdk_mlflow).
- [MLflow OpenCode tracing](https://mlflow.org/docs/latest/genai/tracing/integrations/listing/opencode/) and [OCP 4.20 service CA](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/security_and_compliance/configuring-certificates).
