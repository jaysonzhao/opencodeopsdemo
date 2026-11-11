# OpenCode on OpenShift

This repository consolidates the OpenCode deployment manifests, build assets, operational scripts, and three OpenShift skills. It keeps the direct-Service deployment and the OpenShell sandbox deployment as separate tracks.

## Choose a deployment track

| Track | Use it for | Files |
| --- | --- | --- |
| OpenShell 0.1.7 | A sandboxed OpenCode server with mediated egress, MiniMax or another OpenAI-compatible provider, MLflow tracing, and authenticated forwarding | [`skills/deploy-opencode-openshell-017/SKILL.md`](skills/deploy-opencode-openshell-017/SKILL.md) |
| Direct internal Service | A simpler OpenCode 1.18.3 deployment exposed only through a ClusterIP, with Secret-backed provider settings and MLflow tracing | [`opencode-server-build/`](opencode-server-build/), [`skills/deploy-opencode-openshift-mlflow/SKILL.md`](skills/deploy-opencode-openshift-mlflow/SKILL.md) |
| Repair legacy OpenShell | Repair and token-renewal guidance for existing OpenShell 0.1.2 / MiniMax Global deployments | [`skills/repair-opencode-openshell-mlflow/SKILL.md`](skills/repair-opencode-openshell-mlflow/SKILL.md) |

Do not apply both deployment tracks into one namespace without reviewing resource ownership and naming. The OpenShell skill is the current path for new sandboxed deployments; the direct-Service and repair skills preserve earlier operational workflows.

## Repository layout

- `opencode-server-build/`: direct-Service image source, manifests, health check, and client smoke program.
- `scripts/create-secrets.py`: reads a local MiniMax key file and creates OpenShift Secrets without printing credential values.
- `scripts/update-image-digest.py`: updates the direct-Service workload and smoke-client image references to the validated ImageStream digest.
- `skills/deploy-opencode-openshell-017/`: OpenShell 0.1.7 skill, server and supervisor image assets, renderers, credential helpers, and version references.
- `skills/deploy-opencode-openshift-mlflow/`: direct-Service deployment skill.
- `skills/repair-opencode-openshell-mlflow/`: legacy deployment repair and MLflow renewal skill.
- [`docs/disconnected-deployment.md`](docs/disconnected-deployment.md): image mirroring and offline-build considerations.

## Direct-Service deployment quick start

This path uses namespace `opencode` in its example manifests. Adapt the namespace, storage class, MLflow service details, and image digest to the target cluster. The checked-in digest and MLflow values are deployment examples, not portable discovery results.

```bash
oc -n opencode apply -f opencode-server-build/build.yaml
oc -n opencode start-build opencode-server --from-dir=opencode-server-build --follow --wait
python3 scripts/update-image-digest.py --namespace opencode
oc -n opencode apply --dry-run=server -f opencode-server-build/workload.yaml
oc -n opencode apply -f opencode-server-build/workload.yaml
oc -n opencode rollout status deployment/opencode-server --timeout=10m
```

Create provider credentials separately with `scripts/create-secrets.py --namespace opencode --key-file ~/work/minimax.key`. The key stays in the local file and the OpenShift Secret; do not place it in manifests, ConfigMaps, build arguments, logs, or Git.

## OpenShell and Dev Spaces access

Read and follow the complete OpenShell skill before deploying. Its client access uses two forwards: `oc port-forward` to the gateway and `openshell forward start` to the sandbox's OpenCode service. From an authorized Dev Spaces terminal after the client and protected login file have been provisioned:

```bash
scripts/start-devspaces-forwards.sh
. ~/.config/opencode-remote/auth.env
opencode attach http://127.0.0.1:4096 --dir /sandbox/workspace
```

The helper targets `openshell-opencode-017` by default; set `GATEWAY` to another registered alias when needed. Run it again after a Dev Spaces workspace recreation. It restores the loopback-only gateway `oc port-forward`, checks authentication to that gateway, and recreates the sandbox's local OpenCode forward. It expects the workspace's `oc` login, OpenShell 0.1.7 client configuration/mTLS files, and protected OpenCode login file to remain available; it does not provision credentials.

To create a second sandbox in the prepared `cluster-9blcx` environment, run these scripts from this repository in the Dev Spaces terminal. The creation script pins the existing qualified image digest, inherits the source sandbox base policy, reuses the existing `opencode-model` and `opencode-mlflow` providers, and refuses to overwrite a sandbox with the same name. Its nonsecret settings are in [`deployments/cluster-9blcx/runtime.json`](deployments/cluster-9blcx/runtime.json); the MiniMax key and MLflow token stay in the existing provider/Secret workflow.

```bash
./scripts/create-opencode-sandbox.sh
./scripts/attach-devspaces-opencode.sh --check
./scripts/attach-devspaces-opencode.sh --smoke
./scripts/attach-devspaces-opencode.sh
```

The second sandbox is named `opencode-server-2` and uses local port `4096`, matching the OpenCode listener on port `4096` inside the sandbox. OpenShell 0.1.7 forwards the same port number on both ends, so use the same local port when switching the Dev Spaces forward between the original and second sandbox. Only one sandbox can own that local port at a time. The creation helper defaults to `250m` CPU, which fit the tested cluster, and lets OpenShift schedule it on an available node. Set `CPU` to fit the target cluster, or set `PIN_SOURCE_NODE=true` when the source node has enough capacity and node affinity is required. Sandbox names may be at most 19 characters. `--check` validates authenticated server health, `--smoke` makes a marked MiniMax request, and the default action starts `opencode attach` for `/sandbox/workspace`. Override `SANDBOX`, `CPU`, `PIN_SOURCE_NODE`, `LOCAL_OPENCODE_PORT`, `GATEWAY`, or `RUNTIME_JSON` for another instance; review the runtime settings before using them on a different cluster.

If `openshell forward start` reports `connect error: transport error`, check `openshell --gateway openshell-opencode-017 --workspace default status`. A `tcp connect error: Connection refused` means the configured gateway endpoint has no live listener at `127.0.0.1:18081`; rerun the helper to start or repair the gateway tunnel and recreate the OpenCode forward. The forward list's `alive` field reports the local forward process state; it does not prove that the OpenCode listener answered. Run `scripts/attach-devspaces-opencode.sh --check` to verify the authenticated health endpoint. Set `GATEWAY` to the intended registered alias when it is not `openshell-opencode-017`.

The client receives only OpenCode server Basic-auth credentials and the gateway mTLS client material. The MiniMax key and MLflow token remain server-side. Dev Spaces and OpenShell paths are scoped to one trusted user or group; gateway client certificates grant broad gateway control and must not be shared with untrusted users.

For the existing `cluster-9blcx` OpenShell deployment's static MLflow ServiceAccount credential and manual rotation procedure, see [`docs/opencode-internal-static-mlflow-token.md`](docs/opencode-internal-static-mlflow-token.md).

## Disconnected clusters

See [`docs/disconnected-deployment.md`](docs/disconnected-deployment.md) before using these builds in a disconnected or restricted-network cluster. Mirroring container images does not provide access to the external MiniMax API. A fully disconnected deployment needs a reachable internal OpenAI-compatible model endpoint or an explicitly permitted egress path.

## Local-only deployment state

Generated rendered manifests, local gateway client configuration, certificates, downloaded binaries, logs, and runtime settings are not part of this repository. The working tree ignores `opencode-openshell-lab/`; keep live credentials and private keys outside Git.
