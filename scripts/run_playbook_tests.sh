#!/usr/bin/env bash
set -euo pipefail

# Runs all deterministic, offline Ansible playbook tests that require no live models,
# external API keys, or sandbox clusters.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PLAYBOOKS=(
  "tests/test_checklist_execution.yml"
  "tests/test_guardian_scan.yml"
  "tests/test_merge_dedup.yml"
  "tests/test_merge_findings_string_encoding.yml"
  "tests/test_merge_self_refuted_filter.yml"
  "tests/test_neutralization_check.yml"
  "tests/test_persisted_path_collision.yml"
  "tests/test_pipeline_stats.yml"
  "tests/test_pr_merge_base_diff.yml"
  "tests/test_resolve_target_pr_parsing.yml"
  "tests/test_sandbox_unreachable_teardown.yml"
  "tests/test_verify_score_recompute.yml"
)

echo "Running ${#PLAYBOOKS[@]} offline Ansible playbook test(s)..."

for pb in "${PLAYBOOKS[@]}"; do
  echo "--- Running ${pb} ---"
  ansible-playbook "${pb}"
done

echo "All ${#PLAYBOOKS[@]} playbook tests passed successfully."
