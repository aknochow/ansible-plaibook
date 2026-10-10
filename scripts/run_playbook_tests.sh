#!/usr/bin/env bash
set -euo pipefail

# Runs all deterministic, offline Ansible playbook tests that require no live models,
# external API keys, or sandbox clusters.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
export ANSIBLE_CONFIG="${REPO_ROOT}/ansible.cfg"
# CI sets this to the job's collection install. A local run uses the
# cache `plai` already populated, so the playbook tests do not depend
# on ~/.ansible/collections.
if [[ -z "${ANSIBLE_COLLECTIONS_PATH:-}" ]]; then
  _collections="${HOME}/.cache/ansible-plaibook/collections"
  if [[ -d "${_collections}/ansible_collections" ]]; then
    export ANSIBLE_COLLECTIONS_PATH="${_collections}"
  fi
fi

PLAYBOOKS=(
  "tests/test_briefing_snapshot.yml"
  "tests/test_checklist_execution.yml"
  "tests/test_commit_range.yml"
  "tests/test_cursor_named_lens_retry.yml"
  "tests/test_cursor_lens_async_wait.yml"
  "tests/test_cursor_lens_attempt_usage.yml"
  "tests/test_cursor_prompt_nonce.yml"
  "tests/test_guardian_cache.yml"
  "tests/test_guardian_not_installed.yml"
  "tests/test_guardian_scan.yml"
  "tests/test_guardian_scan_sandboxed.yml"
  "tests/test_merge_dedup.yml"
  "tests/test_merge_findings_string_encoding.yml"
  "tests/test_merge_self_refuted_filter.yml"
  "tests/test_neutralization_check.yml"
  "tests/test_persist_sandbox_artifact.yml"
  "tests/test_persisted_path_collision.yml"
  "tests/test_pipeline_stats.yml"
  "tests/test_pr_ci_preflight.yml"
  "tests/test_pr_merge_base_diff.yml"
  "tests/test_resolve_target_pr_parsing.yml"
  "tests/test_sandbox_unreachable_teardown.yml"
  "tests/test_screen_diff_comments.yml"
  "tests/test_verify_score_recompute.yml"
  "tests/test_version_alignment.yml"
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
  if [[ "${pb}" == "tests/test_cursor_named_lens_retry.yml" || "${pb}" == "tests/test_cursor_lens_async_wait.yml" ]]; then
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

# Private dir + file the failure callback will accept. A clean play must
# leave the file empty. A planned ignored failure must name the task and
# the message, and must not keep a token from the failure text.
prepare_failure_log() {
  local dir log
  dir="$(mktemp -d "${TMPDIR:-/tmp}/plaibook-task-failures-XXXXXX")"
  chmod 700 "${dir}"
  log="${dir}/plaibook-task-failures-run.log"
  : >"${log}"
  chmod 600 "${log}"
  printf '%s\n' "${log}"
}

run_failure_log_playbook() {
  local pb="$1"
  local expect="$2"
  local log out rc=0
  log="$(prepare_failure_log)"
  out="$(mktemp)"
  cleanup_failure_log() {
    rm -f "${out}"
    if [[ -n "${log}" ]]; then
      rm -rf "$(dirname "${log}")"
    fi
  }
  set +e
  PLAIBOOK_TASK_FAILURES_LOG="${log}" ansible-playbook "${pb}" >"${out}" 2>&1
  rc=$?
  set -euo pipefail
  cat "${out}"
  if [[ "${rc}" -ne 0 ]]; then
    echo "::error file=${pb},title=Playbook test failed::${pb} exited ${rc}"
    cleanup_failure_log
    return "${rc}"
  fi
  if [[ "${expect}" == "empty" ]]; then
    if [[ -s "${log}" ]]; then
      echo "::error file=${pb},title=Failure log was not empty::${log}"
      cat "${log}"
      cleanup_failure_log
      return 1
    fi
  elif [[ "${expect}" == "content" ]]; then
    if ! grep -q "Record a planned failure for the error log" "${log}"; then
      echo "::error file=${pb},title=Failure log missing task name::${log}"
      cat "${log}"
      cleanup_failure_log
      return 1
    fi
    if ! grep -q "planned failure for the error log" "${log}"; then
      echo "::error file=${pb},title=Failure log missing message::${log}"
      cat "${log}"
      cleanup_failure_log
      return 1
    fi
    if grep -q "ghs_secret" "${log}"; then
      echo "::error file=${pb},title=Failure log leaked a token::${log}"
      cleanup_failure_log
      return 1
    fi
  elif [[ "${expect}" == "loop-failed" ]]; then
    if [[ "$(grep -c 'Fail each loop item' "${log}")" -ne 2 ]]; then
      echo "::error file=${pb},title=Loop failure count::${log}"
      cat "${log}"
      cleanup_failure_log
      return 1
    fi
    if grep -q "One or more items failed" "${log}"; then
      echo "::error file=${pb},title=Loop summary was recorded::${log}"
      cat "${log}"
      cleanup_failure_log
      return 1
    fi
  elif [[ "${expect}" == "loop-unreachable" ]]; then
    if [[ "$(grep -c '\[unreachable\]' "${log}")" -ne 2 ]]; then
      echo "::error file=${pb},title=Unreachable loop count::${log}"
      cat "${log}"
      cleanup_failure_log
      return 1
    fi
    if grep -q "All items completed" "${log}"; then
      echo "::error file=${pb},title=Unreachable loop summary was recorded::${log}"
      cat "${log}"
      cleanup_failure_log
      return 1
    fi
  fi
  cleanup_failure_log
}

echo "--- Running tests/test_task_failures_clean.yml ---"
run_failure_log_playbook "tests/test_task_failures_clean.yml" empty
echo "--- Running tests/test_task_failures_log.yml ---"
run_failure_log_playbook "tests/test_task_failures_log.yml" content
echo "--- Running tests/test_task_failures_loop.yml ---"
run_failure_log_playbook "tests/test_task_failures_loop.yml" loop-failed
echo "--- Running tests/test_task_failures_unreachable_loop.yml ---"
run_failure_log_playbook "tests/test_task_failures_unreachable_loop.yml" loop-unreachable

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
