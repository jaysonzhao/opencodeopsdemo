# Create and attach to an OpenCode sandbox

These scripts create a second OpenCode sandbox from the existing `opencode-server` sandbox, then connect a Dev Spaces terminal to it through the OpenShell gateway. The model and MLflow credentials remain in the existing OpenShell providers; they are not copied to Dev Spaces.

## Defaults

| Setting | Default |
|---|---|
| OpenShift namespace | `opencode-internal` |
| OpenShell gateway and alias | `openshell-opencode-017` |
| OpenShell workspace | `default` |
| Source sandbox | `opencode-server` |
| New sandbox | `opencode-server-2` |
| Local gateway forward | `127.0.0.1:18081` |
| Local OpenCode forward | `127.0.0.1:4096` |
| Remote working directory | `/sandbox/workspace` |

The new sandbox name must be at most 19 characters. `create-opencode-sandbox.sh` refuses to overwrite an existing sandbox.

## Requirements

- `oc` logged into the cluster that hosts `opencode-internal`.
- Permission to read the existing `opencode-server-auth` and `openshell-dashboard-client-tls` Secrets for a new Dev Spaces workspace, plus access to the gateway Service and the required port-forward operations.
- Existing `opencode-server`, `opencode-model`, and `opencode-mlflow` resources in OpenShell workspace `default`.
- OpenShell CLI 0.1.7. The attach helper installs it in a fresh Linux x86_64 workspace if it is missing. Interactive attachment also needs OpenCode CLI 1.18.3; the default attach action installs it if missing.

## 1. Create the sandbox

Run from this repository in a terminal with a working OpenShell gateway connection and `oc` access:

```bash
bash scripts/create-opencode-sandbox.sh
```

The script uses the source sandbox's digest-pinned image and base policy, binds the existing model and MLflow providers, stages the prepared runtime and server login, and waits for the new sandbox to become ready. It defaults to `opencode-server-2`, `250m` CPU, and `4Gi` memory, and lets OpenShift choose a worker. It does not create or replace provider credentials.

To choose another name or resource size:

```bash
SANDBOX=opencode-server-3 CPU=500m MEMORY=4Gi bash scripts/create-opencode-sandbox.sh
```

If the requested sandbox already exists, inspect it rather than rerunning the create command.

## 2. Prepare the Dev Spaces forwards

In the Dev Spaces terminal, run the authenticated check for the sandbox you want to attach to:

```bash
SANDBOX=opencode-server-2 bash scripts/attach-devspaces-opencode.sh --check
```

For a new workspace without a repository checkout, the same setup can be started from the repository's raw GitHub script:

```bash
curl -LsSf https://github.com/jaysonzhao/opencodeopsdemo/raw/refs/heads/main/scripts/attach-devspaces-opencode.sh \
  | env SANDBOX=opencode-server-2 sh -s -- --check
```

On first use, the helper provisions the protected OpenCode login file and gateway mTLS files from the existing named Secrets, registers the local gateway, and starts both forwards. It prints no Secret values. `--check` then verifies authenticated OpenCode health and returns to the shell prompt, leaving the forwards available for a manual attach.

For the initial setup before creating `opencode-server-2`, bootstrap and check against the existing source sandbox instead:

```bash
curl -LsSf https://github.com/jaysonzhao/opencodeopsdemo/raw/refs/heads/main/scripts/attach-devspaces-opencode.sh \
  | env SANDBOX=opencode-server sh -s -- --check
```

Then run `bash scripts/create-opencode-sandbox.sh` from the downloaded checkout at `~/.cache/opencodeopsdemo` (or `$XDG_CACHE_HOME/opencodeopsdemo`). After creation, rerun the check with `SANDBOX=opencode-server-2` to switch the OpenCode forward to the new sandbox.

An optional marked inference check is:

```bash
SANDBOX=opencode-server-2 bash scripts/attach-devspaces-opencode.sh --smoke
```

`--smoke` checks a model response; verify MLflow trace storage separately when tracing is part of the acceptance criteria.

## 3. Attach manually

After `--check` reports `Authenticated OpenCode health check passed`, source the protected login file and start the OpenCode client in the same Dev Spaces terminal:

```bash
AUTH_FILE="${OPENCODE_AUTH_FILE:-${OPENSHELL_CONFIG_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}}/opencode-remote/auth.env}"
. "$AUTH_FILE"
opencode attach http://127.0.0.1:4096 --dir /sandbox/workspace
```

The attach script's default action performs setup and starts the interactive client automatically. Use `--check` when you want to trigger `opencode attach` yourself. Run the interactive client in the Dev Spaces terminal so it has a real TTY.

If OpenCode CLI 1.18.3 is not installed and you are using the manual attach flow, install it once in the workspace:

```bash
npm install --global --prefix "$HOME/.local" opencode-ai@1.18.3
export PATH="$HOME/.local/bin:$PATH"
```

## Forward recovery

From an existing checkout with the OpenShell client files already provisioned, rerun the lower-level forward helper:

```bash
SANDBOX=opencode-server-2 bash scripts/start-devspaces-forwards.sh
```

It checks the gateway tunnel, replaces the local OpenCode forward for the selected sandbox, and prints the manual attach command. The higher-level `attach-devspaces-opencode.sh --check` is preferred after workspace recreation because it also bootstraps missing local credentials and verifies OpenCode health.

Useful checks:

```bash
openshell --gateway openshell-opencode-017 --workspace default status
openshell forward list --output json
```

The `alive` field confirms the forward process is running; `--check` confirms that the authenticated OpenCode health endpoint answers.

## Keep credentials protected

The helper stores only the OpenCode server login and gateway client certificate in the Dev Spaces home, with restrictive file permissions. Do not copy the MiniMax key, MLflow token, or Kubernetes service-account token into the workspace or repository. Anyone who can read the gateway client private key has broad control over the gateway, so keep the workspace limited to trusted users.
