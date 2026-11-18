#!/usr/bin/env bash
# Reinstalls the aknochow.* collections this project depends on from
# their local source checkouts. Ansible resolves collections from
# ~/.ansible/collections/ansible_collections/aknochow/<name> — that's
# a real copied directory, not a live link to the source repo, so an
# edit to plugins/modules/*.py in any of these repos has NO effect on
# a playbook run here until this script (or the equivalent
# `ansible-galaxy collection install . --force` in that repo) runs
# again. --force is required: without it, ansible-galaxy skips
# reinstalling when it thinks the version hasn't changed, a common
# trap during active dev when galaxy.yml's version isn't bumped every
# commit.
#
# Usage — no default PATHS, since a checked-in dev script shouldn't
# guess at any one contributor's personal directory layout. A collection
# whose env var is left unset (or points at a path that doesn't exist)
# is skipped, not an error — set only the ones for checkouts you have.
#   ANSIBLE_OPENSHELL_DIR=/path/to/ansible-openshell \
#   ANSIBLE_CLAUDE_DIR=/path/to/ansible-claude \
#   ANSIBLE_GEMINI_DIR=/path/to/ansible-gemini \
#     scripts/install-collections.sh

set -euo pipefail

if ! command -v ansible-galaxy >/dev/null 2>&1; then
  echo "ERROR: ansible-galaxy not found on PATH" >&2
  exit 1
fi

ANSIBLE_OPENSHELL_DIR="${ANSIBLE_OPENSHELL_DIR:-}"
ANSIBLE_CLAUDE_DIR="${ANSIBLE_CLAUDE_DIR:-}"
ANSIBLE_GEMINI_DIR="${ANSIBLE_GEMINI_DIR:-}"

collections=(
  "aknochow.openshell:${ANSIBLE_OPENSHELL_DIR}"
  "aknochow.claude:${ANSIBLE_CLAUDE_DIR}"
  # Not yet referenced anywhere else in this repo (no role/playbook uses
  # it, README doesn't list it) -- included ahead of time since
  # ansible-gemini is close to done and about to be pushed publicly.
  "aknochow.gemini:${ANSIBLE_GEMINI_DIR}"
)

failed=()
installed=0

for entry in "${collections[@]}"; do
  name="${entry%%:*}"
  dir="${entry#*:}"

  if [ -z "$dir" ]; then
    echo "SKIP  ${name}: no env var set for its checkout path"
    continue
  fi

  if [ ! -d "$dir" ]; then
    echo "SKIP  ${name}: ${dir} does not exist"
    continue
  fi

  echo "==> Installing ${name} from ${dir}"
  if (cd "$dir" && ansible-galaxy collection install . --force); then
    echo "OK    ${name}"
    installed=$((installed + 1))
  else
    echo "FAIL  ${name}"
    failed+=("$name")
  fi
  echo
done

if [ "${#failed[@]}" -gt 0 ]; then
  echo "Failed to install: ${failed[*]}" >&2
  exit 1
fi

if [ "$installed" -eq 0 ]; then
  echo "No collections installed — no checkout paths were set or found." >&2
  exit 2
fi

echo "All collections installed."
