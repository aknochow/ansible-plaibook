---
type: Guide
title: Getting Started
description: Run your first review and read its output correctly.
tags: [quickstart, review, cli]
status: stable
---

# Getting Started

The short command is **`plai`**. The package and full command are
**`plaibook`**. They share one entry point. After `uv sync` /
`pip install -e .` from this checkout, `plai review ...` and
`plaibook review ...` are the same program. v1 does not upload to
PyPI; install from the checkout. AAP / execution-environment jobs keep
calling `ansible-playbook review.yml` directly.

## Review a GitHub PR or GitLab MR

```bash
uv run plai review org/repo#123
uv run plaibook review org/repo#123
uv run plai review https://github.com/org/repo/pull/12
uv run plai review org/repo!34
```

These assume the plaibook env is installed (`uv sync` from the
checkout). The CLI locates `review.yml` itself and leaves the caller's
cwd alone, so you can review another repo without `cd`. Override the
checkout with `--root` / `PLAIBOOK_ROOT` if needed.

AAP / execution-environment runs still invoke the playbook:

```bash
ansible-playbook review.yml -e review_targets_raw="org/repo#123"
```

`review_targets_raw` accepts a GitHub PR URL, a GitLab MR URL, or a bare
`org/repo#N` (GitHub) / `org/repo!N` (GitLab) identifier. Pass several
targets at once on the playbook path as a newline-separated string, or
use the JSON-list form:

```bash
uv run ansible-playbook review.yml -e '{"review_targets": ["org/repo#1", "org/repo#2"]}'
```

## Review a single local commit: fast and cheap

```bash
uv run plai review --commit
uv run plaibook review --commit
uv run plai review --commit --sha abc1234 --repo /path/to/repo
```

Both arguments are optional (`--sha` defaults to `HEAD`, `--repo` to
the current directory). This mode skips the sandbox and the
exploration pass. It only sees the diff itself, not the surrounding
codebase, so it's fast and inexpensive, at the cost of missing anything
that requires reading a file outside the diff. A `NEEDS_CHANGES`
verdict with a real Critical/Major finding exits non-zero, on both
`plai` and `plaibook`.

## CLI output

Default is quiet (no ansible task wall). `-v` prints full
`ansible-playbook` output. `--json` and `--yaml` write the structured
summary on stdout and nothing else, so skills can pipe them. The CLI
reads `~/.cache/ansible-plaibook/last_run.<run_id>.json`; it does not
scrape playbook stdout or recompute scores.

## Reading the playbook artifacts

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
  "started_at": "2026-09-08T13:46:41+00:00",
  "finished_at": "2026-09-08T13:54:32+00:00",
  "duration_seconds": 471,
  "phase_durations_seconds": {
    "guardian_scan": 0,
    "lenses": 265,
    "merge": 1,
    "explore": 84,
    "verify": 113,
    "persist": 2,
    "unattributed": 6
  },
  "total_input_tokens": 12345,
  "total_output_tokens": 6789,
  "agents_dispatched": 3
}
```

If more than one session might be reviewing at the same time, don't
trust this shared path. `plai review --json` already prints the
run-scoped copy. On the playbook path, read the run-scoped file
(printed as `RESULT_SUMMARY_RUN_SCOPED:`) instead, since concurrent
invocations race to overwrite the shared file.

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
uv run plai review org/repo!34 --post
# AAP / EE:
uv run ansible-playbook review.yml -e review_targets_raw="org/repo!34" -e post_results=true
```
