#!/usr/bin/env bash
set -euo pipefail

# Runs all deterministic, offline Ansible playbook tests that require no live models,
# external API keys, or sandbox clusters.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
export ANSIBLE_CONFIG="${REPO_ROOT}/ansible.cfg"

PLAYBOOKS=(
  "tests/test_checklist_execution.yml"
  "tests/test_cursor_named_lens_retry.yml"
  "tests/test_cursor_lens_attempt_usage.yml"
  "tests/test_cursor_prompt_nonce.yml"
  "tests/test_guardian_not_installed.yml"
  "tests/test_guardian_scan.yml"
  "tests/test_merge_dedup.yml"
  "tests/test_merge_findings_string_encoding.yml"
  "tests/test_merge_self_refuted_filter.yml"
  "tests/test_neutralization_check.yml"
  "tests/test_persisted_path_collision.yml"
  "tests/test_pipeline_stats.yml"
  "tests/test_pr_ci_preflight.yml"
  "tests/test_pr_merge_base_diff.yml"
  "tests/test_resolve_target_pr_parsing.yml"
  "tests/test_sandbox_unreachable_teardown.yml"
  "tests/test_screen_diff_comments.yml"
  "tests/test_verify_score_recompute.yml"
)

echo "Running ${#PLAYBOOKS[@]} offline Ansible playbook test(s)..."

# dispatch_cursor_lens_attempt.yml calls aknochow.cursor.agent. The
# named-lens retry playbook swaps in tests/library/cursor_agent_stub.py
# (no live Cursor) via ANSIBLE_LIBRARY + cursor_agent_module.
STUB_LIBRARY="${REPO_ROOT}/tests/library"
STUB_PAYLOAD="${REPO_ROOT}/tests/.cursor_agent_stub.json"

run_playbook() {
  local pb="$1"
  local out rc=0
  out="$(mktemp)"
  set +e
  if [[ "${pb}" == "tests/test_cursor_named_lens_retry.yml" ]]; then
    ANSIBLE_LIBRARY="${STUB_LIBRARY}${ANSIBLE_LIBRARY:+:${ANSIBLE_LIBRARY}}" \
      CURSOR_AGENT_STUB_FILE="${STUB_PAYLOAD}" \
      ansible-playbook "${pb}" >"${out}" 2>&1
  else
    ansible-playbook "${pb}" >"${out}" 2>&1
  fi
  rc=$?
  set -euo pipefail
  cat "${out}"
  if [[ "${rc}" -ne 0 ]]; then
    echo "::error file=${pb},title=Playbook test failed::${pb} exited ${rc}"
    rm -f "${out}"
    return "${rc}"
  fi
  rm -f "${out}"
}

for pb in "${PLAYBOOKS[@]}"; do
  echo "--- Running ${pb} ---"
  run_playbook "${pb}"
done

echo "--- Running tests/run_cursor_sidecar_skip.sh ---"
bash "${REPO_ROOT}/tests/run_cursor_sidecar_skip.sh"

echo "--- Syntax-check review.yml without optional collections ---"
# AAP Vertex EEs do not ship aknochow.cursor. Play-level FQCNs would
# fail at parse; this empty COLLECTIONS_PATH is that environment.
empty_collections="$(mktemp -d)"
ANSIBLE_COLLECTIONS_PATH="${empty_collections}" ANSIBLE_CALLBACKS_ENABLED="" \
  ansible-playbook "${REPO_ROOT}/review.yml" --syntax-check \
  -e agent_family=gemini -e use_sandbox=false
rmdir "${empty_collections}"

echo "All ${#PLAYBOOKS[@]} playbook tests plus sidecar skip scenarios passed successfully."
