#!/usr/bin/env bash
# Install the pinned OpenShell gateway on a CI runner and pull the sandbox image.
# Reads .github/openshell-pins.env values from the environment.
set -euo pipefail

installer="$RUNNER_TEMP/openshell-install.sh"
curl -fsSL "https://raw.githubusercontent.com/NVIDIA/OpenShell/${OPENSHELL_INSTALLER_COMMIT}/install.sh" -o "$installer"
echo "${OPENSHELL_INSTALLER_SHA256}  $installer" | sha256sum -c -
OPENSHELL_VERSION="$OPENSHELL_VERSION" sh "$installer"
if [ -x "$HOME/.local/bin/openshell" ]; then
  echo "$HOME/.local/bin" >> "$GITHUB_PATH"
  export PATH="$HOME/.local/bin:$PATH"
fi
openshell status

export OPENSHELL_WORKSPACE=default OPENSHELL_PROVISION_TIMEOUT=180
# The pinned sandbox image embeds /etc/openshell/policy.yaml. On this gateway
# that policy stays ConfigurationInvalid until the 300s repair window expires.
# A global policy with no network rules replaces it.
cat > "$RUNNER_TEMP/plaibook-sandbox-policy.yaml" <<'EOF'
version: 1
filesystem_policy:
  include_workdir: true
  read_only:
    - /usr
    - /lib
    - /bin
    - /proc
    - /dev/urandom
    - /etc
    - /var/log
  read_write:
    - /sandbox
    - /tmp
    - /dev/null
landlock:
  compatibility: best_effort
EOF
openshell policy set --global --yes --policy "$RUNNER_TEMP/plaibook-sandbox-policy.yaml"
if ! timeout 300 openshell sandbox create \
  --name plaibook-image-warm \
  --from "$SANDBOX_IMAGE" \
  --detach \
  --no-tty; then
  openshell sandbox get plaibook-image-warm || true
  journalctl --user -u openshell-gateway --no-pager -n 40 || true
  exit 1
fi
if [ "${OPENSHELL_SMOKE:-false}" = "true" ]; then
  openshell sandbox exec -n plaibook-image-warm -- sh -c 'echo sandbox-ready'
fi
openshell sandbox delete plaibook-image-warm
