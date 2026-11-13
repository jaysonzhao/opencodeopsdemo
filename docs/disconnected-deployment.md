# Disconnected OpenCode and OpenShell deployment

This runbook prepares the repository's OpenShell 0.1.7 / OpenCode 1.18.3 / MLflow 3.14 deployment for an OpenShift cluster that cannot pull public images or download public packages. It covers image inventory, digest locking, `oc-mirror` v2, chart and CLI transfer, deployment configuration, and inference/tracing acceptance. A short direct-Service track is included for the retained `opencode-server-build/` deployment.

The supported pattern builds the final application image in a controlled connected environment, then mirrors the final image and every image in the rendered OpenShell chart. The target cluster does not run `git clone`, npm, pip, or other public package downloads during deployment. Building inside the disconnected cluster is a separate effort that requires a complete source and package mirror.

## Network boundaries and readiness

Registry reachability and model reachability are separate controls.

- The OpenShell gateway, supervisor, sandbox runtime, and final OpenCode image must be available from the disconnected registry. The OpenShift nodes and sandbox service accounts need the required pull access.
- MLflow uses the existing in-cluster OpenShift AI service. Confirm its actual Service URL, workspace authorization, and CA chain on the target cluster.
- `https://api.minimax.io/v1` is a hosted endpoint. A cluster with no external egress cannot call it. Use an approved egress path through the existing exact OpenShell destination policy, or supply a reachable internal OpenAI-compatible endpoint and validate its API prefix, certificate, model ID, streaming, and tool calls.
- Registry mirroring does not make model traffic reachable. Do not work around either failure by disabling TLS validation or opening unrestricted sandbox egress.

## Inputs and ownership

Prepare these values before staging:

| Input | Requirement |
|---|---|
| Target | OpenShift version, architecture, cluster registry mirror policy, and internal registry host/path |
| OpenShell | CLI, chart, gateway, supervisor, and trusted runtime all pinned to `0.1.7` |
| Application | OpenCode `1.18.3`; MLflow SDK and plugin source `v3.14.0`; final image published by digest |
| Agent Sandbox | Existing compatible CRDs/controller. If it must be installed or upgraded, mirror its operator/catalog and image set through the platform operator's separate procedure. |
| Model | Exact API URL and model ID. For fully disconnected clusters, this must be an internal OpenAI-compatible service. |
| MLflow | Observed internal HTTPS Service URL/prefix and an authorized MLflow workspace |
| Storage and trust | Approved block StorageClass, internal registry CA trust, and MLflow/model destination CA trust |

Use a supported registry reachable by every cluster node and capable of the image operations required by `oc-mirror` v2. OpenShift's integrated image registry might not satisfy the mirror destination requirements; confirm this with the platform owner before staging.

Keep API keys, MLflow tokens, client certificates, registry pull credentials, kubeconfigs, and generated gateway config outside the bundle and Git. The model key and MLflow credential remain in the server-side OpenShift/OpenShell credential flow; the client receives only its OpenCode login and gateway client material.

## 1. Build on a connected staging host

Use a trusted build host or CI runner with access to the pinned upstream source, GitHub, npm, PyPI, the required base images, and the staging registry. Follow the image build instructions in [the OpenShell skill](../skills/deploy-opencode-openshell-017/SKILL.md) and [internal-server reference](../skills/deploy-opencode-openshell-017/references/internal-server.md). Pin the base image by digest, record source revisions and build provenance, and publish the **final** OpenCode/MLflow image to a staging registry. Do not send package-registry credentials or the MiniMax key to the cluster build.

The final runtime image already contains the OpenCode binary, the MLflow plugin, Python SDK, and runtime dependencies. Promote that final image; do not require npm/PyPI from the disconnected cluster. If policy requires the build to happen offline, vendor the complete MLflow source, npm lockfile closure/cache, Python wheelhouse for the exact runtime ABI/architecture, OpenCode package, and digest-pinned builder/runtime bases. The build must fail if any network fetch is attempted.

## 2. Download and verify non-image artifacts

Create a private staging directory; its contents are deployment artifacts, not source files:

```bash
set -euo pipefail
umask 077
export BUNDLE="$PWD/opencode-disconnected-bundle"
mkdir -p "$BUNDLE/chart" "$BUNDLE/cli" "$BUNDLE/rendered" "$BUNDLE/mirror"

helm pull oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.7 --destination "$BUNDLE/chart"
cp skills/deploy-opencode-openshell-017/assets/server/gateway-values.yaml \
  "$BUNDLE/rendered/gateway-values.yaml"
```

Download the platform-specific OpenShell 0.1.7 CLI release artifact required by the trusted control host (and any separately required prover artifact). Verify its vendor-published checksum/signature before use. Record the selected platform and tool versions in the bundle manifest. Copy the exact deployment skill, assets, scripts, and this runbook into the bundle so the disconnected operator has the matching procedure.

Keep CLI verification evidence, the pulled chart version and digest, and registry authentication on the staging host. Generate the transit checksum manifest only after the rendered files, image lock, and any image-set archive are present. Do not include registry credentials, API keys, certificates, runtime settings containing secrets, or kubeconfig data.

## 3. Render and lock every deployment image

Render the exact chart package and values that will be installed. Rendering the chart is the inventory source for gateway init containers and all other chart-created pods. Add images that are selected later by the driver, including the final application image used by `openshell sandbox create --from`.

```bash
export CHART=$(find "$BUNDLE/chart" -maxdepth 1 -name '*.tgz' -print -quit)
test -n "$CHART"
helm template openshell-opencode-017 "$CHART" \
  --namespace opencode-internal \
  -f "$BUNDLE/rendered/gateway-values.yaml" \
  --set fullnameOverride=openshell-opencode-017 \
  --set server.dataStorageClass=YOUR_BLOCK_STORAGE_CLASS \
  --set server.workspaceStorageClass=YOUR_BLOCK_STORAGE_CLASS \
  --set agentSandbox.preflight.enabled=false \
  > "$BUNDLE/rendered/gateway.yaml"

# The final app image is passed directly to `openshell sandbox create --from`.
printf '%s\n' 'STAGING_REGISTRY/TEAM/opencode-server@sha256:REPLACE_WITH_PUBLISHED_DIGEST' \
  > "$BUNDLE/rendered/extra-images.txt"

python3 scripts/prepare-disconnected-images.py \
  --manifest "$BUNDLE/rendered/gateway.yaml" \
  --images-file "$BUNDLE/rendered/extra-images.txt" \
  --platform linux/amd64 \
  --output-dir "$BUNDLE/mirror"

helm template openshell-opencode-017 "$CHART" \
  --namespace opencode-internal \
  -f "$BUNDLE/rendered/gateway-values.yaml" \
  -f "$BUNDLE/mirror/openshell-image-digests.yaml" \
  --set fullnameOverride=openshell-opencode-017 \
  --set server.dataStorageClass=YOUR_BLOCK_STORAGE_CLASS \
  --set server.workspaceStorageClass=YOUR_BLOCK_STORAGE_CLASS \
  --set agentSandbox.preflight.enabled=false \
  > "$BUNDLE/rendered/gateway-digests.yaml"
python3 scripts/prepare-disconnected-images.py \
  --manifest "$BUNDLE/rendered/gateway-digests.yaml" \
  --images-file "$BUNDLE/rendered/extra-images.txt" \
  --platform linux/amd64 \
  --output-dir "$BUNDLE/mirror"
```

The helper uses `skopeo inspect --raw` to resolve and lock all references, verifies existing digest references, checks each image supports the selected platform, and writes:

- `images.lock.json`: requested reference, normalized source, and immutable digest reference;
- `ImageSetConfiguration.yaml`: `oc-mirror` v2 configuration for the complete image set;
- `openshell-image-digests.yaml`: the OpenShell chart image digest overrides for the pinned gateway, sandbox runtime, supervisor, and default sandbox image when those images are present in the rendered chart.

Review the lock against the rendered YAML and deployment driver inputs. If a chart image is reported as unmapped, set its chart value to the locked digest or use the platform-approved mirror rule for that exact reference. Re-render the final chart and confirm every image in every container and init container resolves to a locked source. Include any helper, migration, sidecar, certificate, or init image that is enabled by your values. Do not assume that the gateway image is the only OpenShell image.

Use `openshell-image-digests.yaml` as a second Helm values file and render again to `gateway-digests.yaml`. Run the image-lock helper against that final render and the same extra image list; retain the final lock and image set. Confirm all OpenShell gateway/supervisor/runtime image references in the final render use the locked digest.

The Agent Sandbox operator/controller and OpenShift AI/MLflow operators are cluster-level dependencies and are not owned by this application chart. If they are not already installed, have the platform owner include their exact operator catalogs, bundles, and operand images in the platform image set. The Agent Sandbox controller must be compatible with the chart and present before the OpenShell gateway is installed.

### Direct-Service deployment

The direct-Service path uses `opencode-server-build/Containerfile` and `opencode-server-build/workload.yaml`; its current build downloads the MLflow source and npm/PyPI dependencies. Do not run that BuildConfig in a disconnected cluster as-is. Build the final image in controlled connected CI, push it to the staging registry, add its digest reference to `extra-images.txt`, and render the workload with that final digest before generating the image set. On the disconnected side, use the already-built image and deploy the workload; do not start the source build. Include any non-application images from the workload manifest in the same lock. Keep MLflow and model reachability gates identical to the OpenShell path.

If policy requires builds inside the disconnected cluster, create a separate reviewed offline build context: mirror the UBI Node builder and OpenCode base images by digest; vendor the pinned MLflow source; provide the complete npm cache/lock closure, OpenCode native package, and target-ABI Python wheelhouse; and remove all build steps that contact GitHub, npm, PyPI, or public registries. Exercise that build against a network-denied builder before accepting it. This repository's standard build files are not claimed to satisfy that hermetic-build requirement.

## 4. Mirror with `oc-mirror` v2

Use the `oc-mirror` v2 plugin version recommended by the current target-version Red Hat documentation (the OpenShift 4.20 guide recommends the latest available plugin) and an authenticated image-registry configuration. The plugin's credentials are local to the mirror host and must not be copied into the bundle.

For an online mirror host that can reach both upstream registries and the target registry:

```bash
oc mirror --v2 \
  --config "$BUNDLE/mirror/ImageSetConfiguration.yaml" \
  --workspace "file://$BUNDLE/mirror/workspace" \
  docker://INTERNAL_REGISTRY/openshift-mirror
```

For a transfer that crosses an air gap, create an image archive on the connected side:

```bash
oc mirror --v2 \
  --config "$BUNDLE/mirror/ImageSetConfiguration.yaml" \
  "file://$BUNDLE/mirror/image-set"
```

On the connected side, after the archive is ready, checksum and transfer the chart, CLI, rendered files, image lock/config, and image-set archive. For mirror-to-mirror promotion, use the same checksum flow for the chart, CLI, and rendered bundle:

```bash
(cd "$BUNDLE" && find . -type f ! -name SHA256SUMS -print0 \
  | xargs -0 shasum -a 256 > SHA256SUMS)
```

After transfer, verify with `(cd "$BUNDLE" && shasum -a 256 -c SHA256SUMS)`. Then on the disconnected side, import the archive with the matching `ImageSetConfiguration.yaml`:

```bash
oc mirror --v2 \
  --config "$BUNDLE/mirror/ImageSetConfiguration.yaml" \
  --from "file://$BUNDLE/mirror/image-set" \
  docker://INTERNAL_REGISTRY/openshift-mirror
```

Review the generated `cluster-resources` directory from the import and have a cluster administrator apply the generated `ImageDigestMirrorSet` resources. Apply any other generated resources only when required by the organization's registry policy. Confirm the internal registry has every locked digest and that cluster nodes trust the registry CA and can authenticate to it.

If the environment uses explicit internal image references instead of OpenShift mirror policies, map each source digest from `images.lock.json` to the corresponding target digest and set those exact target references in the rendered chart values and sandbox creation command. Do not rely on tag rewriting or a node's image cache.

## 5. Install from the local chart and start the sandbox

On the disconnected cluster, verify the transfer checksums, apply the mirror policy, and use only the local chart archive. Configure chart values using the generated digest overrides, retain the OpenShift SCC-compatible values and security settings, and set narrowly scoped pull access. Use the standard deployment runbook for gateway installation, runtime qualification, MLflow authorization, model Secret handling, sandbox creation, forwarding, and credential renewal.

```bash
export BUNDLE=/path/to/transferred/opencode-disconnected-bundle
export CHART=$(find "$BUNDLE/chart" -maxdepth 1 -name '*.tgz' -print -quit)
test -n "$CHART"
helm upgrade --install openshell-opencode-017 \
  "$CHART" \
  --namespace opencode-internal \
  --values "$BUNDLE/rendered/gateway-values.yaml" \
  --values "$BUNDLE/mirror/openshell-image-digests.yaml" \
  --set fullnameOverride=openshell-opencode-017 \
  --set server.dataStorageClass=YOUR_BLOCK_STORAGE_CLASS \
  --set server.workspaceStorageClass=YOUR_BLOCK_STORAGE_CLASS \
  --wait --timeout 10m
```

Set `SERVER_IMAGE` to the locked staging reference included in the same mirror set. If using explicit internal references, set it to the corresponding internal-registry digest. Never use a mutable tag as the sandbox image. Confirm the node's actual `imageID` for each pod equals the approved mirror digest. Restart-test a worker with no previously cached copy before calling the image path ready.

The chart is published as an OCI artifact; the disconnected install must use the transferred local `.tgz`, not the original OCI URL. OpenShell gateway/service access remains private and authenticated. Do not add a public Route, NodePort, LoadBalancer, or direct Service for sandbox port 4096.

## 6. Prove the deployment is usable

Use the acceptance gates in the OpenShell skill. For disconnected readiness, evidence must include:

1. Every chart, init, operator-owned (when applicable), supervisor, sandbox-runtime, and application image resolves to an approved locked digest in the internal registry.
2. A sandbox starts on a worker without a cached image and passes the OpenShell 0.1.7 runtime/socket qualification under the intended restricted SCC and enforced CNI policy.
3. The trusted client authenticates through the gateway and attaches to the server on sandbox loopback. Anonymous API access fails.
4. A normal useful model response and a tool-based code change succeed through the configured endpoint. For hosted MiniMax, demonstrate the specifically approved egress path; for a fully disconnected cluster, demonstrate the internal provider endpoint.
5. The matching MLflow trace appears in the intended workspace with normal TLS verification.
6. A deliberate unknown registry image and unauthorized egress attempt fail. Do not report successful inference or tracing from Ready pods, mirror records, or HTTP reachability alone.

If the cluster cannot reach `api.minimax.io` and no internal compatible model endpoint exists, image deployment may be ready but model inference is blocked. Report that boundary clearly.

## Version references

- [OpenShift 4.20 disconnected image mirroring with `oc-mirror` v2](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/disconnected_environments/about-installing-oc-mirror-v2)
- [OpenShift `oc-mirror` v2 image-set workflow](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/disconnected_environments/installing-mirroring-disconnected)
- [OpenShell v0.1.7 chart source and image overrides](https://github.com/NVIDIA/OpenShell/blob/v0.1.7/deploy/helm/openshell/values.yaml)
- [OpenShell v0.1.7 chart install and dependency notes](https://github.com/NVIDIA/OpenShell/blob/v0.1.7/deploy/helm/openshell/README.md)
