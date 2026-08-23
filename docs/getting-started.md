---
type: Guide
title: Getting Started
description: Run your first review and read its output correctly.
tags: [quickstart, review, cli]
status: stable
---

# Getting Started

## Review a GitHub PR or GitLab MR

```bash
ansible-playbook review.yml -e review_targets_raw="org/repo#123"
ansible-playbook review.yml -e review_targets_raw="https://github.com/org/repo/pull/12"
ansible-playbook review.yml -e review_targets_raw="org/repo!34"
```

`review_targets_raw` accepts a GitHub PR URL, a GitLab MR URL, or a bare
`org/repo#N` (GitHub) / `org/repo!N` (GitLab) identifier. Pass several
targets at once as a newline-separated string, or use the JSON-list form:

```bash
ansible-playbook review.yml -e '{"review_targets": ["org/repo#1", "org/repo#2"]}'
```

## Review a single local commit: fast and cheap

```bash
ansible-playbook review.yml -e review_type=commit
ansible-playbook review.yml -e review_type=commit -e commit_sha=abc1234 -e repo_path=/path/to/repo
```

Both arguments are optional (`commit_sha` defaults to `HEAD`, `repo_path`
to the current directory). This mode skips the sandbox and the
exploration pass. It only sees the diff itself, not the surrounding
codebase, so it's fast and inexpensive, at the cost of missing anything
that requires reading a file outside the diff.

## Reading the output

Every run writes one predictable file, overwritten each run:

```
~/.cache/ansible-plaibook/last_run.json
```

```json
{
  "targets": [
    {
      "target": "org/repo#123",
      "report": "<full rendered findings.md text>",
      "verdict": "READY_FOR_HUMAN_REVIEW | NEEDS_CHANGES",
      "score": 8.3,
      "summary_path": "/path/to/summary.json",
      "findings_path": "/path/to/findings.md"
    }
  ],
  "cost_usd": 0.1234,
  "total_input_tokens": 12345,
  "total_output_tokens": 6789,
  "agents_dispatched": 3
}
```

If more than one session might be reviewing at the same time, don't
trust this shared path. Read the run-scoped copy instead (the path is
printed as `RESULT_SUMMARY_RUN_SCOPED:` at the end of the run), since
concurrent invocations race to overwrite the shared file.

Drill into a target's `summary_path` for the full structured
`summary.json`: verdict, per-lens scores, and every finding with its
file, line, severity, evidence, and verification status.

## Verdict rule

Any surviving Critical or Major finding forces `NEEDS_CHANGES`, no
confidence carve-out. Per-lens and overall scores are recomputed
deterministically from the surviving findings list itself, never read
directly from a lens's own self-reported score line, since a lens can
verbally retract a finding without recomputing the number that goes with
it.

## Post the review back to the real PR/MR (opt-in)

Reviewing is safe to automate by default; posting is a write to shared
state and requires explicit opt-in:

```bash
ansible-playbook review.yml -e review_targets_raw="org/repo!34" -e post_results=true
```
