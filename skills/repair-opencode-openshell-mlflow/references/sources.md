# Sources and validation scope

Reviewed 2026-11-10. These primary sources support the design; the per-request file reload and renewal publisher are custom integration code, not an upstream built-in Kubernetes refresh feature.

- [OpenShell 0.1.2 providers](https://docs.nvidia.com/openshell/v0.1.2/how-it-works/providers/overview): ordinary static updates, readiness and new-process environment behavior.
- [OpenShell 0.1.2 profiles](https://docs.nvidia.com/openshell/v0.1.2/how-it-works/providers/profiles): endpoint-bound references, provider policy and native refresh/token-grant mechanisms.
- [OpenShell 0.1.2 source](https://github.com/NVIDIA/OpenShell/tree/v0.1.2): `crates/openshell-cli/src/main.rs`, supervisor credential snapshots and resolver tests, `crates/openshell-supervisor-network/data/sandbox-policy.rego` and `crates/openshell-core/src/endpoint_path.rs`.
- [OpenShift runtime notes](https://docs.nvidia.com/openshell/v0.1.2/kubernetes/openshift) and [support matrix source](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/docs/about/support-matrix.mdx): capability-free driver, Landlock/seccomp requirements and legacy read-only limitations.
- [MLflow 3.14.0 OSS auth](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/core/src/auth/index.ts), [request helper](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/core/src/clients/utils.ts), [trace client](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/core/src/clients/client.ts) and [artifact client](https://github.com/mlflow/mlflow/blob/v3.14.0/libs/typescript/core/src/clients/artifacts/mlflow.ts): cached initial bearer header and per-request headers-provider callbacks.
- [OpenCode integration](https://github.com/mlflow/mlflow/tree/v3.14.0/libs/typescript/integrations/opencode): tracing plugin and idle-event export behavior.
- [OpenShift AI 3.5 SDK authentication](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-and-authenticating-mlflow-sdk_mlflow): workspace and authentication modes. The direct Kubernetes auth plugin is not enabled in this sandbox because real Kubernetes credentials stay outside it.
- [Kubernetes ServiceAccount tokens](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/): TokenRequest and expiry. Inspect actual `exp` rather than assuming the requested lifetime was honored.
- [Kubernetes authorization check CLI](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_auth/kubectl_auth_can-i/): named-resource and subresource syntax.
- [MiniMax OpenCode guide](https://platform.minimax.io/docs/m-plan/opencode) and [starter-kit baseline](https://github.com/red-hat-data-services/agentic-starter-kits/tree/838546a258193667ed79845d70196c274bbff7f7/agents/opencode).

Use the already supplied `opencode-openshift-mlflow-SKILL.md` portable bundle for the unchanged base deployment, trusted supervisor build and credentials/staging helper. This repair's image-preparation script verifies the exact baseline source before modifying a new build directory. It does not overwrite that bundle or an existing live image.

Local validation must exercise the same auth-provider instance across two file generations, rejection of invalid/expired handles, preservation of the previous file after probe failure, create/update command secrecy, no lifecycle operations during renewal and the baseline-patch guard. Live image build, OpenShell reference resolution and after-expiry trace delivery remain target-cluster acceptance gates.
