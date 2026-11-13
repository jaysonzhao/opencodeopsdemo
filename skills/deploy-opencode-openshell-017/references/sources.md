# Primary sources and adaptation boundaries

Reviewed 2026-11-09. Use matching pinned source when current documentation differs.

| Area | Primary source |
|---|---|
| Starter kit | [OpenCode at commit 838546a](https://github.com/red-hat-data-services/agentic-starter-kits/tree/838546a258193667ed79845d70196c274bbff7f7/agents/opencode) and [MLflow auth/TLS](https://github.com/red-hat-data-services/agentic-starter-kits/blob/838546a258193667ed79845d70196c274bbff7f7/docs/mlflow-openshift-auth-and-tls.md) |
| OpenShell source/CLI | [v0.1.2 source](https://github.com/NVIDIA/OpenShell/tree/v0.1.2), especially `crates/openshell-cli/src/main.rs`, `deploy/helm/openshell`, and `crates/openshell-driver-kubernetes` |
| OpenShift runtime qualification | [v0.1.2 OpenShift notes](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/docs/kubernetes/openshift.mdx) |
| Forwarding and lifecycle | [v0.1.2 sandboxes](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/docs/how-it-works/sandboxes/overview.mdx) |
| Credential profiles/rotation | [v0.1.2 providers](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/docs/how-it-works/providers/overview.mdx) and [profiles](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/docs/how-it-works/providers/profiles.mdx) |
| Policy | [v0.1.2 schema](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/docs/how-it-works/policies/schema.mdx), [updates](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/docs/how-it-works/policies/manage-policies.mdx), and built-in `providers/anthropic.yaml` |
| Supervisor trust | [TLS source](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/crates/openshell-supervisor-network/src/l7/tls.rs) and [supervisor image](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/deploy/docker/Dockerfile.supervisor) |
| MiniMax Global | [OpenCode setup](https://platform.minimax.io/docs/m-plan/opencode) and [Anthropic-compatible API](https://platform.minimax.io/docs/api-reference/text-anthropic-api) |
| OpenCode | [v1.18.3 source](https://github.com/anomalyco/opencode/tree/v1.18.3), especially provider, config, plugin and attach implementations; [server API](https://docs.opencode.ai/docs/server/) |
| MLflow client/plugin | [v3.14.0 OpenCode integration](https://github.com/mlflow/mlflow/tree/v3.14.0/libs/typescript/integrations/opencode) and [core auth](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/core/src/auth/index.ts) |
| OpenShift AI 3.5 | [MLflow installation/RBAC](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-mlflow_mlflow) and [SDK/authentication](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-and-authenticating-mlflow-sdk_mlflow) |
| Service trust | [OCP 4.20 certificate configuration](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/security_and_compliance/configuring-certificates) |
| Disconnected image promotion | [OpenShift 4.20 oc-mirror v2](https://docs.openshift.com/container-platform/4.20/disconnected_environments/about-installing-oc-mirror-v2.html), including additional images and mirror-to-disk/import workflows |
| Agent Sandbox controller | [v1.0.9 release](https://github.com/kubernetes-sigs/agent-sandbox/releases/tag/v1.0.9) |

The foreground launcher, policy/profile renderer, credential staging/renewal helper, supervisor CA image and Helm storage post-renderer are this skill's integration code. They are not upstream one-command support guarantees. Source review and local tests do not prove the combined images, seccomp listener, CNI path or SDK/backend compatibility on a particular cluster. The runbook's live acceptance gates remain required.

The upstream kit's OpenShell README uses older versions, privileged SCC and short-lived tokens exported into the sandbox. This adaptation instead pins a matching Kubernetes driver stack and routes credentials through provider handles. The standalone legacy workflow remains separately documented and has a different projected-token mechanism; do not combine their token procedures.

## Internal server update — 2026-11-10

Use these sources for the new default 0.1.7 / configurable OpenAI-compatible track; earlier sections describe the retained 0.1.2 MiniMax and direct-Service tracks.

- [Requested starter kit, pinned](https://github.com/red-hat-data-services/agentic-starter-kits/tree/36b2678d1a8e71db25e59fc138d0c7fda3d50a50/agents/opencode): studied README, OpenShell/MLflow Containerfiles, provider template, entrypoint, tracing setup and shared MLflow auth/TLS guidance. Adapt the provider and plugin integration; do not inherit its old privileged runtime, direct root exec, read-only POST rules, insecure TLS, runtime package installs or delete/recreate instructions.
- [OpenShell v0.1.7 release](https://github.com/NVIDIA/OpenShell/releases/tag/v0.1.7), source commit `5d25211b25a8fc887e8d5c606d57d6ab22f1d76c`.
- [0.1.7 support matrix](https://docs.nvidia.com/openshell/v0.1.7/about/support-matrix) and [OpenShift guide](https://docs.nvidia.com/openshell/v0.1.7/kubernetes/openshift): active Landlock ABI 3, nested seccomp, task-memory read and socket device-binding requirements; normal accepted-socket peer semantics. The tagged guide requires OpenShift 4.19+ because older RHCOS kernels lack Landlock. This skill targets 4.20+, with runtime tests still required.
- [0.1.7 provider profiles](https://docs.nvidia.com/openshell/v0.1.7/how-it-works/providers/profiles): stable externally updated static credentials, expiry, supporting-component checks, endpoint binding and activation. Stable value rotation does not preserve references across provider replacement or endpoint changes.
- [0.1.7 gateway configuration](https://docs.nvidia.com/openshell/v0.1.7/how-it-works/gateways/configuration) and [tagged Helm values](https://github.com/NVIDIA/OpenShell/blob/v0.1.7/deploy/helm/openshell/values.yaml): destination CA ConfigMap, explicit gateway data StorageClass, shared-workspace driver configuration and restart requirements.
- [OpenCode v1.18.3 provider implementation](https://github.com/anomalyco/opencode/blob/v1.18.3/packages/opencode/src/provider/provider.ts): bundled `@ai-sdk/openai-compatible` and v1 provider handling. Current unversioned OpenCode docs can use v2 `providers`/`settings`/`plugins`; do not transplant them into this v1 configuration.
- [MLflow v3.14.0 OpenCode integration](https://github.com/mlflow/mlflow/tree/v3.14.0/libs/typescript/integrations/opencode) and [OSS auth source](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/core/src/auth/index.ts): built-in plugin, cached initial bearer header and workspace header. Native stable OpenShell references make value renewal possible without modifying that cached header. MLflow release tag v3.14.0 is not the npm plugin's package version.
- [OpenShift AI 3.5 MLflow overview](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/about-mlflow_mlflow) and [SDK authentication and compatibility](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-and-authenticating-mlflow-sdk_mlflow): shared service, project workspace, Kubernetes-backed authorization and SDK/backend compatibility.

Validation scope: local helper syntax/execution, rendered policy/profile structure, Helm rendering, deferred-input rejection and forward review can be completed without a cluster. Container image build/publication, admitted runtime and CNI checks, model tool/stream support, trace delivery and after-expiry continuity require the actual target services. Do not label those live gates verified from source review.
