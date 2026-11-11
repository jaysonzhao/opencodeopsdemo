# OpenCode MLflow static credential

This operator record covers the existing OpenShell 0.1.7 deployment in cluster `cluster-9blcx`. It preserves the current gateway, default workspace, `opencode-server` sandbox, `opencode-mlflow` provider, profile, MLflow endpoint, workspace, experiment, ServiceAccount, and RBAC.

## Live binding

- Namespace: `opencode-internal`
- Gateway: `opencode-internal-017`, OpenShell 0.1.7
- Sandbox: `opencode-server`
- Provider/profile: `opencode-mlflow` / `opencode-rhoai-mlflow`
- MLflow URI: `https://mlflow.redhat-ods-applications.svc.cluster.local:8443/mlflow`
- MLflow workspace and experiment: `opencode-internal` / `opencode-server-traces`
- ServiceAccount and Secret: `opencode-tracer` / `opencode-mlflow-static-token`
- Rotation owner: OpenCode deployment operator; rotation and revocation are manual.

The ServiceAccount token Secret is type `kubernetes.io/service-account-token`, annotated for `opencode-tracer`, and must remain outside the ServiceAccount's `.secrets` list, image pull secrets, and sandbox mounts. The existing MLflow authorization binding is retained. The OpenShell profile uses `stable_placeholder` for `MLFLOW_TRACKING_TOKEN`; the agent only sees the opaque endpoint-bound reference.

The local, nonsecret runtime and mode record are kept in the ignored lab state directory:

- `opencode-openshell-lab/generated-cluster-9blcx/runtime.json`
- `opencode-openshell-lab/generated-cluster-9blcx/mlflow-credential-mode.json`

The runtime was copied from the live sandbox's nonsecret settings and validated against `render_server.py`. Do not commit generated live state, Kubernetes credentials, provider exports, or token values.

## Migration record

- Candidate status: validated against the existing MLflow experiment (`experiment_id=2`) over verified HTTPS; the candidate JWT has no `exp` claim.
- Provider recorded expiry before activation: `2026-11-11T09:15:53Z`.
- Renewal writer checks: no matching process on the Mac, active Dev Spaces terminal, or supplied bastion; no matching cluster CronJob was found.
- Provider activation: completed at `2026-11-11T07:34:25Z`; provider expiry metadata is cleared and OpenShell confirmed the same sandbox/session/process-instance IDs.
- Authenticated smoke: passed with MiniMax-M2.7-highspeed. Marker `opencode-internal-daf93eaa66280210` matched MLflow trace `tr-55e700903462ff1c13bccfdd597ae0d5` in experiment `2`.
- Sandbox credential check: `MLFLOW_TRACKING_TOKEN` remains an opaque `openshell:` reference. Linux OpenCode PID continuity was not checked.
- Trace-after-expiry check: pending for `2026-11-11T09:15:53Z` or later. Clearing provider expiry metadata does not itself extend an expiring JWT; do not report this deferred check as passed.

The helper [`migrate_mlflow_static_token.py`](../skills/deploy-opencode-openshell-017/scripts/migrate_mlflow_static_token.py) implements the prepare, activate, verify, and rollback steps. It uses the secret-free OpenShell provider list for identity and expiry metadata. Keep `MIGRATION_DIR` private; the helper never prints or places the token in a process argument.

Typical invocation from a trusted operator host:

```bash
set -euo pipefail
set +x
umask 077
export NS=opencode-internal
export GATEWAY=opencode-internal-017 WORKSPACE=default SANDBOX=opencode-server
export TRACE_PROVIDER=opencode-mlflow TRACER=opencode-tracer
export STATIC_SECRET=opencode-mlflow-static-token
export OPENSHELL_BIN="$PWD/opencode-openshell-lab/bin/openshell-0.1.7"
export OPENSHELL_CONFIG_HOME="$PWD/opencode-openshell-lab/client-config-opencode-017"
export RUNTIME="$PWD/opencode-openshell-lab/generated-cluster-9blcx/runtime.json"
export MIGRATION_DIR=/path/to/private/migration-state
export MLFLOW_CA_BUNDLE="$PWD/opencode-openshell-lab/trust/mlflow-service-ca.crt"
export MLFLOW_PROBE_HOST=127.0.0.1
python3 skills/deploy-opencode-openshell-017/scripts/migrate_mlflow_static_token.py prepare
```

For this local setup, keep these forwards open in separate terminals while running the helper:

```bash
oc -n opencode-internal port-forward service/openshell-opencode-017 18081:8080 --address=127.0.0.1
oc -n redhat-ods-applications port-forward service/mlflow 8443:8443 --address=127.0.0.1
```

The helper's `MLFLOW_PROBE_HOST` setting maps only the local TCP connection to loopback; the HTTPS URL hostname, SNI, and certificate validation stay unchanged. `opencode-openshell-lab/` is ignored by Git and the local mTLS files are owner-only. The new CLI copy is `openshell 0.1.7`; the older lab binary remains unchanged.

Run `activate --renewers-stopped` only after every renewal writer and scheduler for this exact provider has stopped. Then run `verify`, send a marked OpenCode request, and confirm a matching trace in the stated experiment. After the prior credential expiry time, send another marked request and verify another trace without restarting the sandbox. For rollback, use the helper's `rollback --renewers-stopped`, verify the new short-lived token and trace, restore `short-lived` mode, then resume only the original renewal writer.
