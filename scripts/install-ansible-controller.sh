#!/usr/bin/env bash
# Install the public awx.awx pin and expose it as ansible.controller.
#
# AAP ships ansible.controller (Automation Hub). Public Galaxy publishes
# the same modules as awx.awx. This script does not vendor a second MCP
# or a second job-launch implementation. It installs the pinned upstream
# collection and points the ansible.controller FQCN at that tree, with
# _COLLECTION_TYPE=controller so API calls use /api/controller/v2/
# (the platform gateway) rather than AWX's /api/v2/.
#
# If ansible.controller.job_launch is already on ANSIBLE_COLLECTIONS_PATH,
# the script leaves it alone.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${ANSIBLE_COLLECTIONS_PATH:-${ROOT}/.galaxy-collections}"
PIN="24.6.1"

mkdir -p "${DEST}"
export ANSIBLE_COLLECTIONS_PATH="${DEST}"

if ansible-doc ansible.controller.job_launch 2>&1 | grep -q 'job_launch.py'; then
  echo "ansible.controller.job_launch already installed"
  exit 0
fi

ansible-galaxy collection install "awx.awx:${PIN}" -p "${DEST}"

src="${DEST}/ansible_collections/awx/awx"
target="${DEST}/ansible_collections/ansible/controller"
if [[ ! -d "${src}" ]]; then
  echo "awx.awx ${PIN} did not install under ${src}" >&2
  exit 1
fi
mkdir -p "${DEST}/ansible_collections/ansible"
rm -rf "${target}"
cp -a "${src}" "${target}"

python3 - "${target}" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
api = root / "plugins" / "module_utils" / "controller_api.py"
text = api.read_text()
old = '_COLLECTION_TYPE = "awx"'
new = '_COLLECTION_TYPE = "controller"'
if old not in text:
    raise SystemExit("controller_api.py has no _COLLECTION_TYPE = awx to retarget")
api.write_text(text.replace(old, new, 1))
galaxy = root / "galaxy.yml"
if galaxy.exists():
    lines = []
    for line in galaxy.read_text().splitlines():
        if line.startswith("namespace:"):
            lines.append("namespace: ansible")
        elif line.startswith("name:"):
            lines.append("name: controller")
        else:
            lines.append(line)
    galaxy.write_text("\n".join(lines) + "\n")
PY

if ! ansible-doc ansible.controller.job_launch 2>&1 | grep -q 'job_launch.py'; then
  echo "ansible.controller.job_launch is not resolvable after install" >&2
  exit 1
fi
echo "ansible.controller.job_launch ready from awx.awx ${PIN}"
