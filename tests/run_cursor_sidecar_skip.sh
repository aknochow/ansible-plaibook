#!/usr/bin/env bash
# lookup('ansible.builtin.env') reads the ansible-playbook process env,
# not a task's environment: directive. Drive the skip playbook with a
# real process environment per scenario.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PLAYBOOK="tests/test_cursor_sidecar_skip.yml"

run_case() {
  local name="$1"
  local expect="$2"
  shift 2
  echo "--- sidecar skip: ${name} (expect wanted=${expect}) ---"
  env -u CURSOR_SDK_BRIDGE_URL \
    -u CURSOR_SDK_BRIDGE_TOKEN \
    -u CURSOR_SDK_BRIDGE_AUTH_TOKEN \
    -u CURSOR_SDK_BRIDGE_URL_FILE \
    -u CURSOR_SDK_BRIDGE_TOKEN_FILE \
    CURSOR_AGENT=1 \
    "$@" \
    ansible-playbook "${PLAYBOOK}" -e "expect_sidecar_wanted=${expect}"
}

run_case "no attach" true
run_case "URL+token" false \
  CURSOR_SDK_BRIDGE_URL=http://127.0.0.1:9 \
  CURSOR_SDK_BRIDGE_TOKEN=test-not-a-real-token
run_case "URL_FILE+TOKEN_FILE" false \
  CURSOR_SDK_BRIDGE_URL_FILE=/tmp/ansible-plaibook-test-bridge-url \
  CURSOR_SDK_BRIDGE_TOKEN_FILE=/tmp/ansible-plaibook-test-bridge-token
run_case "URL_FILE only" true \
  CURSOR_SDK_BRIDGE_URL_FILE=/tmp/ansible-plaibook-test-bridge-url

echo "All sidecar skip scenarios passed."
