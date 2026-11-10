---
name: deploy-opencode-openshell-017
description: Deploy an internal OpenCode server inside an OpenShell sandbox on OpenShift 4.20+ using the Red Hat agentic starter kit. Configure a later-defined OpenAI-compatible API and OpenShift Secret, authenticated local or Dev Spaces access, persistent code and sessions, and tracing to the existing OpenShift AI 3.5 MLflow service. Use for runtime qualification, exact egress and provider profiles, service CA trust, workspace RBAC, renewable MLflow credentials, verification and downloadable deployment instructions. Retain earlier MiniMax and direct-Service tracks for explicit legacy maintenance.
---

# Internal OpenCode server on OpenShift

For disconnected or restricted-network targets, read [../../docs/disconnected-deployment.md](../../docs/disconnected-deployment.md) before image preparation. Public image/source/package fetches in the connected build examples must be replaced with approved mirrors or prebuilt digest-pinned artifacts.

Use the **OpenShell 0.1.7 internal-server track** for new requests. Read [references/internal-server.md](references/internal-server.md) completely before deployment. Its `assets/server/`, `scripts/render_server.py` and `scripts/server_credentials.py` are required companions. A request to prepare or update this skill authorizes file preparation, not live cluster changes. Report preparation and live acceptance separately.

## Deployment contract

1. Adapt the Red Hat starter kit at commit `36b2678d1a8e71db25e59fc138d0c7fda3d50a50` (reviewed 2026-11-10). Pin OpenShell CLI/chart/gateway/supervisor/trusted runtime **0.1.7**, OpenCode **1.18.3**, and MLflow Python SDK plus TypeScript plugin **source tag v3.14.0**. The plugin's npm version is separate from the MLflow release tag. Do not copy newer OpenCode v2 configuration into this v1 runtime.
2. Keep OpenShift **4.20+** as the platform target, with live qualification required. The user's reported 4.20.39 / RHCOS 9.6 / kernel 5.14.0-570.141.1 workers failed socket accept under OpenShell 0.1.2. Released 0.1.7 changes that implementation; validate it in a separate gateway and sandbox. Do not infer that the old `WAIT_KILLABLE_RECV` limitation applies unchanged to 0.1.7, or that upgrading alone proves success.
3. Require Landlock ABI >=3, nested seccomp user notification and atomic FD injection, task-memory reads, capability-free socket device binding and effective ingress/egress NetworkPolicy. Require the supplied socket probe, authenticated HTTP and streamed model output. Preserve `restricted-v2` compatibility; never use privilege, syscall shims, disabled seccomp or `best_effort` as a repair.
4. Keep OpenCode on sandbox **127.0.0.1:4096**, accessed through authenticated `openshell forward`. Use a ClusterIP gateway Service and loopback `oc port-forward`; create no external Route, LoadBalancer, NodePort or direct sandbox-port Service. In-cluster trusted clients may use the authenticated internal gateway endpoint with a matching certificate SAN. Basic auth is per trusted sandbox, not tenant isolation.
5. Leave unknown endpoint, model, budgets, MLflow URL and Secret values unset in `assets/server/settings.example.json`. Build and qualify infrastructure independently. Generate deployable profiles only after actual settings are supplied; never invent a provider, API key, model or trace result.
6. Use OpenCode v1 `provider.configured`, `@ai-sdk/openai-compatible`, `options.baseURL` and an opaque `MODEL_API_KEY` from OpenShell. The base URL includes the API prefix (usually `/v1`), not `/chat/completions`. Keep the real API key in an existing or explicitly created OpenShift Secret and the endpoint-bound OpenShell provider. Require model streaming and tool-calling compatibility. Responses-only APIs need a separately reviewed adapter.
7. Use the existing OpenShift AI **3.5 MLflow service**, its actual HTTPS URL/prefix, authorized workspace and observed integration role. Issue short-lived tokens for a separate `opencode-tracer` service account outside the sandbox. Enable native `stable_placeholder` only on supporting 0.1.7 components; renew and wait for activation without routine OpenCode restarts. Do not mount Kubernetes tokens or kubeconfigs into the agent.
8. Trust MLflow/model private CA chains through `supervisor.network.additionalCaConfigMapName`; keep the workload's injected proxy CA variables intact. Do not use insecure TLS. CA changes require gateway restart and explicit affected-sandbox stop/start; document this lifecycle.
9. Keep workspace, OpenCode SQLite/WAL, cache and sessions under persistent block-backed `/sandbox`. Use a dedicated namespace, independent from Dev Spaces, and a separate gateway for incompatible version migrations. Preserve existing Secrets, PVCs, worktrees, providers and original sandboxes.
10. Restrict model egress to the fixed OpenCode executable and exact endpoint paths. Restrict MLflow to OpenCode and the resolved Python executable. Build plugins/toolchains into the image; arbitrary Internet, package installs and Git egress stay closed until needed and authorized.

## Execute and verify

Follow the runbook in order: inspect ownership and versions; prepare deferred settings; build/publish images; install or reuse a qualified gateway; test actual socket behavior; fill provider settings and configure MLflow RBAC; import profiles and credentials; create/stage the foreground server; forward and attach; verify inference, code context, traces and token renewal.

Require all of the following before reporting success:

- Matching trusted runtime versions and intended worker placement; runtime and CNI checks pass.
- Internal authenticated connection; anonymous application session access rejected; correct remote workspace.
- Actual configured-model response, interactive streaming and a successful tool-based code change.
- Matching trace in the intended MLflow workspace, with model/tool spans where emitted.
- Denied unauthorized executable/destination checks and preserved state after the intended lifecycle test.
- A new matching trace after the previous JWT expires, with the same OpenCode process and sandbox alive.

Use `server_credentials.py attach` for interactive access and `smoke` for a marked API request. Keep `renew-loop` running on a trusted control host with valid `oc` and gateway authentication. Losing the renewer eventually stops tracing; the skill does not install an unattended scheduler or guarantee recovery of lost traces.

## Earlier deployments

- [references/openshell.md](references/openshell.md), `assets/openshell/`, `render_openshell.py` and `credentials.py` retain the earlier **0.1.2 + MiniMax Global** workflow for explicit maintenance and compatibility with existing repair bundles. They are not the new generic-provider path. Keep their assets unchanged unless deliberately migrating that old workflow.
- [references/direct-server.md](references/direct-server.md) retains the earlier direct internal Service deployment. Use it only if the user explicitly changes scope away from OpenShell.
- [references/sources.md](references/sources.md) records primary sources and version boundaries.

Do not use legacy source hashes, custom MLflow reference-file patches, old static-token restart commands or direct pod access in the new 0.1.7 track.
