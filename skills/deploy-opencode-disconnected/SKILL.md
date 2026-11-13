---
name: deploy-opencode-disconnected
description: Prepare, mirror, deploy, and qualify the OpenCode 1.18.3 server with OpenShell 0.1.7 and OpenShift AI MLflow in disconnected or restricted-network OpenShift clusters. Use for offline image inventories, digest locks, oc-mirror v2 image sets, local Helm chart and CLI handoff, mirrored registry policy, internal model endpoints, and disconnected deployment acceptance.
---

# Disconnected OpenCode/OpenShell deployment

Use this skill as the disconnected deployment overlay for [`deploy-opencode-openshell-017`](../deploy-opencode-openshell-017/SKILL.md). Read that skill and its complete `references/internal-server.md` first. Then follow [`docs/disconnected-deployment.md`](../../docs/disconnected-deployment.md) in order. This skill adds the connected staging, offline transfer, and mirror-policy requirements; it does not replace OpenShell runtime qualification, MLflow authorization, secret handling, forwarding, or renewal steps.

For the retained direct-Service deployment, follow the `Direct-Service deployment` section in the runbook and [`deploy-opencode-openshift-mlflow`](../deploy-opencode-openshift-mlflow/SKILL.md). That path mirrors the final workload image and any other manifest images; it does not need the OpenShell chart or gateway CLI.

## Target contract

- OpenShift 4.20+; OpenShell CLI, chart, gateway, supervisor, and trusted sandbox runtime 0.1.7; OpenCode 1.18.3; MLflow SDK and OpenCode plugin source tag v3.14.0.
- Build the final application image with all npm/Python/runtime dependencies included before image promotion. Use the exact locked digest at sandbox creation.
- Use the exact local Helm chart archive, not an OCI URL, on the disconnected side.
- Inventory the rendered chart's containers and init containers and add the final application image selected in the OpenShell driver. Include the Agent Sandbox operator/catalog and operand image set only when the platform dependency itself must be installed or upgraded.
- Preserve the gateway mTLS boundary, private ClusterIP, restricted-v2 compatibility, enforced ingress/egress NetworkPolicy, service/destination CA trust, namespace-scoped storage/RBAC, and server-side credentials.
- Keep model access and registry access as separate network paths. A fully disconnected cluster cannot use hosted `https://api.minimax.io/v1`; require an approved egress path or a reachable internal OpenAI-compatible service.

## Procedure

1. Inspect the target cluster and resource ownership. Confirm the target OpenShift release, architecture, registry mirror trust/pull permissions, block StorageClass, Agent Sandbox CRD/controller, CNI NetworkPolicy enforcement, MLflow service/workspace, and model endpoint reachability plan. Stop before deployment if a required shared operator dependency is missing; have its platform owner install or mirror it.
2. On a connected trusted staging host, build and publish the final OpenCode/MLflow image from the pinned Containerfile. Pin all builder/runtime bases by digest and preserve the source revision, build logs, SBOM/provenance, and platform. Never include API keys or tokens in build inputs.
3. Pull the exact `0.1.7` Helm OCI artifact and required trusted-client CLI/prover release assets. Verify vendor checksums/signatures. Copy the exact skill assets, nonsecret values, and scripts alongside them.
4. Render the downloaded chart with deployment values and target release name. Include every image reference chosen outside the chart, especially the final server image passed to `openshell sandbox create --from`.
5. Run `scripts/prepare-disconnected-images.py` over the rendered manifests and extra image list with the target platform (the current deployment assets target `linux/amd64`). Review `images.lock.json`, `ImageSetConfiguration.yaml`, and `openshell-image-digests.yaml`; resolve every unmapped chart image. The digest lock must account for every container and init container in the final render.
6. Use the `oc-mirror` v2 version recommended by the current OpenShift documentation (the 4.20 guide recommends the latest available plugin) to mirror those image digests to the internal registry, directly or through a transferred image-set archive. Apply the generated image mirror resources with the cluster-admin process. Keep registry auth outside the bundle. Transfer and checksum the chart, CLI, image-set, rendered files, and lock metadata.
7. On the disconnected side, verify the transfer checksums and digest presence. Install/upgrade from the local chart archive and digest override values. Apply only the reviewed mirror resources. Configure registry pull access and CA trust. Never rely on public registry access, package downloads, mutable tags, or cached images.
8. Complete the normal OpenShell runbook: qualify the runtime/socket behavior before credentials; configure the actual OpenAI-compatible model and OpenShift Secret; authorize the separate MLflow service account; create the sandbox; forward and attach through authenticated local ports.
9. Prove all image IDs against the approved mirror digests, restart on a worker without cached images, test authenticated streaming inference and a tool action, verify the trace in the correct MLflow workspace, and test denied registry/model egress. A ready pod is not application acceptance.

## Required reporting

Report the image inventory and lock, chart/CLI checksums, mirror resources applied, target pull verification, runtime qualification, model response, and matching MLflow trace as separate evidence. If external MiniMax is unreachable, state that deployment/image readiness is distinct from inference readiness and leave the model profile unset until the internal endpoint or approved egress path is confirmed. Do not claim an air-gapped smoke test by substituting a dummy key or endpoint.

## Reference

- Full steps and commands: [`docs/disconnected-deployment.md`](../../docs/disconnected-deployment.md)
- Main deployment and runtime acceptance: [`deploy-opencode-openshell-017/SKILL.md`](../deploy-opencode-openshell-017/SKILL.md)
- OpenShift 4.20 `oc-mirror` v2 docs: [mirroring disconnected images](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/disconnected_environments/about-installing-oc-mirror-v2)
- OpenShell 0.1.7 chart image values: [pinned source](https://github.com/NVIDIA/OpenShell/blob/v0.1.7/deploy/helm/openshell/values.yaml)
