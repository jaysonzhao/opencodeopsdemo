# Disconnected and restricted-network deployments

This guide covers promoting the repository's OpenCode/OpenShell images into a disconnected or restricted-network OpenShift environment. It does not claim that the checked-in build is fully hermetic: the Containerfiles fetch upstream sources and packages unless a controlled build process mirrors them first.

## Separate image access from model access

An OpenShift cluster can be disconnected from public registries while still having an approved egress path to a model API. These are separate network decisions.

- The configured MiniMax endpoint, `https://api.minimax.io/v1`, requires outbound HTTPS reachability from the OpenShell-mediated request path. A fully air-gapped cluster cannot use that hosted endpoint. Point the provider at an internal OpenAI-compatible service, or arrange an explicitly approved egress path and matching OpenShell policy.
- MLflow should remain at the observed internal OpenShift AI service URL with normal certificate validation.
- Do not solve registry or model reachability failures by disabling TLS verification or widening the sandbox policy to arbitrary Internet access.

## Build and inventory before the cluster is disconnected

Use a controlled connected build host or CI runner with the required source and package access. Pin and record digests for every input. The current source paths fetch or depend on:

- OpenShell 0.1.7 Helm chart and gateway/runtime images.
- The supervisor and sandbox images selected by the rendered chart, Agent Sandbox integration, and OpenShell driver configuration.
- The OpenCode server base image and the resulting custom OpenCode/MLflow image.
- The UBI Node builder, MLflow source tag `v3.14.0`, npm dependencies, Python wheels, and the OpenCode 1.18.3 native CLI package used during image construction.
- OpenShell 0.1.7 CLI/prover release artifacts needed by the operator or Dev Spaces client.

Use the target version's actual render and driver configuration as the inventory source; do not assume the gateway image is the only image the sandbox will pull. For the OpenShell track, render the pinned chart using the target values, inspect every `image:` in the output, and separately record the sandbox runtime image and supervisor image from the selected driver/operator configuration. Include init containers and certificate-generation images. For the direct-Service track, include the custom server image and every build-stage base image if builds will happen inside the isolated environment.

For each source image, record its source digest, target mirror reference, architecture, and resulting target digest in a deployment-specific lock file kept with the deployment records. Preserve the original upstream reference and provenance. Use the internal registry references or an OpenShift registry mirror configuration that the cluster administrators approve. Grant only the gateway, builder, and sandbox identities the required pull access.

Pull and save the pinned Helm chart and CLI/prover release artifacts in the connected staging area. Verify each vendor-published checksum/signature before promotion. Copy the chart and image archives through the approved transfer process, then push them into the internal registry/repository. Do not commit binaries, certificates, kubeconfigs, or credential files to this source repository.

## Build requirements

The current Containerfiles use `git clone`, npm, pip, and public base-image references. For deployment into a disconnected cluster:

1. Prefer building the final OpenCode and CA-aware supervisor images in controlled CI while the required sources and package registries are available.
2. Pin base images by digest and retain the source revision, dependency lockfiles, build logs, and image SBOM/provenance with the promoted image.
3. Push or export the final images, mirror them into the disconnected registry, and update the rendered deployment values/manifests to use those internal digest-pinned references.
4. If policy requires rebuilding inside the disconnected environment, first vendor the source archives, npm package closure, and a complete Python wheelhouse. The current `pip install mlflow[kubernetes]==3.14.0` line pins the top-level version but does not by itself lock every transitive package; prepare and review a hash-locked offline dependency set before calling that build reproducible.

The OpenShell chart is an OCI artifact. Mirror the exact `0.1.7` chart package as well as its container images. Keep its chart version, gateway, supervisor, sandbox runtime, CLI, and prover versions aligned with the deployment skill's compatibility checks.

## Configure the disconnected cluster

- Set explicit internal registry references in the rendered Helm values, driver configuration, sandbox template, and application workload. If using cluster-level image mirror policies instead, verify the mirror policy is supported and accepted by the cluster's OpenShift version.
- Configure narrowly scoped `imagePullSecrets` or the approved global pull-secret/mirror setup. Trust the internal registry CA through the supported cluster or workload configuration.
- Keep `imagePullPolicy: IfNotPresent` for digest-pinned internal images. Do not rely on mutable tags or an image already cached on one worker.
- Preserve the documented OpenShell mTLS boundary, namespace-scoped storage and RBAC, `restricted-v2` constraints, service CA trust, and exact provider/MLflow endpoint policies.
- Keep the model provider URL and egress policy consistent. If using an internal model endpoint, verify its DNS name, certificate SAN/CA, API prefix, model ID, streaming and tool-call support before importing the profile.

## Acceptance evidence

Before declaring the disconnected deployment ready, verify:

1. Every pod and init container starts using an image digest present in the internal registry. Inspect the actual pod `imageID`, not just its requested tag.
2. The gateway and sandbox can restart on a worker with no cached copy and still pull only from approved mirrors.
3. The OpenShell 0.1.7 socket/runtime qualification, client mTLS, application Basic auth, and service forwarding checks pass.
4. A real model response works through the intended internal endpoint or approved external egress path.
5. The matching trace reaches the internal MLflow instance with normal TLS verification.
6. A deliberate unavailable-mirror or unauthorized-egress attempt fails as expected without weakening policy.

If the cluster cannot reach the MiniMax API and no compatible internal model endpoint exists, the platform images may be deployable but inference is not ready.
