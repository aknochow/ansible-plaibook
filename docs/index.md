---
type: Concept
title: ansible-plaibook
description: An Ansible-native AI code-review and bug-fix pipeline, deterministic orchestration around Claude/Gemini/Qwen, not an interactive agent loop.
tags: [overview, review-pipeline, ansible]
status: stable
---

# ansible-plaibook

ansible-plaibook is an **Ansible-native AI code-review and bug-fix pipeline**.
It runs the same review/fix logic a human would perform by hand (reading a
diff, checking it against known-good and known-bad patterns, verifying
findings before trusting them) as a deterministic Ansible playbook rather
than an open-ended interactive agent session. That determinism is the
point: the same invocation always takes the same code path, every dollar
spent is accounted for per call, and the pipeline can be reasoned about
and tested like any other piece of infrastructure.

## Two entry points

| Playbook | Purpose |
|---|---|
| [`review.yml`](getting-started.md) | Review a GitHub PR, GitLab MR, a branch, or a single local commit |
| `bug_pipeline.yml` | Jira-driven autonomous bug fix (GitHub PR creation only) |

## Where to go next

- **[Getting Started](getting-started.md)**: run your first review and read its output correctly.
- **[Architecture](architecture.md)**: how the pipeline is built: the review role, domain-specific steering, and the independent verification pass.
- **[Reference](reference.md)**: every variable that controls a run.

## Why "deterministic" matters here

The only genuinely non-deterministic parts of a run are the live model
calls themselves (the two review lenses, the exploration pass, and the
independent verify pass). Everything else (dedup, scoring, the
self-refuted-finding filter, persistence) is plain, testable Python and
Jinja with no model involved. Findings and scores can vary slightly
between two runs of the same diff; the *mechanism* that turns findings
into a verdict never does.
