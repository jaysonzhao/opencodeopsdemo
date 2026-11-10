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
. ~/.config/opencode-remote/auth.env
opencode attach http://127.0.0.1:4096 --dir /sandbox/workspace
```

The client receives only OpenCode server Basic-auth credentials and the gateway mTLS client material. The MiniMax key and MLflow token remain server-side. Dev Spaces and OpenShell paths are scoped to one trusted user or group; gateway client certificates grant broad gateway control and must not be shared with untrusted users.

## Disconnected clusters

See [`docs/disconnected-deployment.md`](docs/disconnected-deployment.md) before using these builds in a disconnected or restricted-network cluster. Mirroring container images does not provide access to the external MiniMax API. A fully disconnected deployment needs a reachable internal OpenAI-compatible model endpoint or an explicitly permitted egress path.

## Local-only deployment state

Generated rendered manifests, local gateway client configuration, certificates, downloaded binaries, logs, and runtime settings are not part of this repository. The working tree ignores `opencode-openshell-lab/`; keep live credentials and private keys outside Git.
