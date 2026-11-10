---
name: repair-opencode-openshell-mlflow
description: Repair an existing OpenCode/OpenShell setup on OpenShift with MiniMax Global and OpenShift AI MLflow. Use for sandbox startup or kernel qualification failures, localhost port-forward and attach errors, policy or credential denials, MLflow TLS/RBAC/trace failures, and expired tracing tokens. Add a locally patched MLflow client plus an external renewal helper so routine token rotation does not restart OpenCode or its sandbox. Preserve the original sandbox, PVC, secrets and code.
---

# Repair OpenCode, OpenShell and MLflow

Treat an unspecified failure as a diagnostic task, not an established root cause. Prepare files when asked for a skill; do not access or change a live cluster merely to create it. Use the earlier `deploy-opencode-openshift-mlflow` deployment bundle as the baseline.

## Workflow

1. Read [references/repair.md](references/repair.md) completely. Identify the installed versions, exact failing layer and resource ownership. Follow only the relevant repair branch. Never weaken isolation or delete state to make a health check pass.
2. Read [references/renewal.md](references/renewal.md) for expiry without routine restarts. Use its guarded image preparation and dedicated provider. It contains the one-time rollout, bootstrap, external renewal loop, live acceptance and rollback.
3. Consult [references/sources.md](references/sources.md) before changing versions or protocol details. The audited baseline is OpenShell **0.1.2**, OpenCode **1.18.3**, and MLflow client/plugin **3.14.0**. Another version requires reviewing its source/help; do not copy unreleased `dev` flags into this baseline.

## Repair boundaries

- Keep MiniMax Global at `https://api.minimax.io/anthropic/v1` with `@ai-sdk/anthropic`. Keep the account's actual enabled model and tested token budgets. Leave its key in the OpenShift Secret and OpenShell provider; never print it.
- Keep OpenCode listening on sandbox `127.0.0.1:4096`. Use authenticated `openshell forward` and local `opencode attach`. Distinguish the gateway tunnel, supervised exec, sandbox listener and application authentication.
- Require Landlock ABI >=3, supported nested seccomp behavior and effective CNI ingress/egress enforcement. A kernel/server incompatibility needs a qualified supported runtime/node; `privileged`, `anyuid`, disabled seccomp or `tls: skip` are not repairs.
- Preserve `/sandbox`, SQLite/WAL, working trees, credential objects, client authentication and shared gateway/controller settings. Keep the old sandbox/PVC until a requested migration is accepted. Do not patch generated Kubernetes pods behind the OpenShell driver.
- Trust the MLflow service certificate in the OpenShell supervisor's upstream trust store and retain the agent's injected proxy CA variables. Use the actual integration role and authorized MLflow workspace; do not replace it with namespace `edit`.
- The previous static-provider renewal path needs a new process. This repair adds a **custom per-request client patch**, not a claim that OpenShell rewrites a running process's environment.
- The external control process issues the real short-lived JWT, updates the OpenShell provider and waits for readiness. A newly supervised helper receives an opaque reference, verifies MLflow access and publishes it atomically. The running client rereads that file on every request. The real JWT never enters the sandbox or a token file.
- Require one-time installation of the patched runtime. Routine renewals afterward do not stop/start the server or sandbox. Do not claim zero downtime for the initial rollout, unexpired in-flight requests forever, or guaranteed delivery during control-plane outages.
- Use one renewal writer for each dedicated provider/sandbox. The loop must remain running outside the sandbox with valid `oc` and gateway authentication. Failure to renew eventually stops tracing; this code fails closed and does not silently fall back to a stale environment token.

## Bundled implementation

- `scripts/prepare_image.py`: verify the earlier bundle's source hashes and generate a new image build directory without overwriting differing files.
- `assets/patch-auth.cjs`: patch the pinned MLflow TypeScript source before compilation. Reload only an opaque reference from the fixed JSON path, checking expiry on every request.
- `assets/mlflow_reference.py`: verify a new reference, publish it atomically and lock against competing publishers; never print it.
- `assets/server_identity.py`: identify the live OpenCode process without exposing its environment.
- `scripts/renew_mlflow.py`: create the isolated repair provider, bootstrap or renew once, and optionally run the renewal loop. Check actual token expiry and server identity. Never run lifecycle commands during renewal.

## Completion criteria

Require local build/syntax checks plus, on the target cluster: real MiniMax response over the authenticated forward; correct code context; matching MLflow trace; denied unauthorized destination/binary tests; and a new matching trace **after the previous JWT's expiry while the same OpenCode process and sandbox remain alive**. An experiment, provider readiness, a changed file or one immediate trace alone is insufficient. Report any unverified image build, kernel qualification or live acceptance explicitly.
