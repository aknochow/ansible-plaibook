---
type: Concept
title: ansible-plaibook
description: An Ansible-native AI code-review pipeline, deterministic orchestration around Claude/Gemini/Qwen, not an interactive agent loop.
tags: [overview, review-pipeline, ansible]
status: stable
---

# ansible-plaibook

ansible-plaibook is an **Ansible-native AI code-review pipeline**.
It runs the same review logic a human would perform by hand (reading a
diff, checking it against known-good and known-bad patterns, verifying
findings before trusting them) as a deterministic Ansible playbook rather
than an open-ended interactive agent session. That determinism is the
point: the same invocation always takes the same code path, every dollar
spent is accounted for per call, and the pipeline can be reasoned about
and tested like any other piece of infrastructure.

## Entry point

| Playbook | Purpose |
|---|---|
| [`review.yml`](getting-started.md) | Review a GitHub PR, GitLab MR, a branch, or a single local commit |

`plai review` / `plaibook review` wrap `review.yml`. There is no
`bug_pipeline.yml` on `main` and no `plai fix`.

## Where to go next

- **[Getting Started](getting-started.md)**: `plai review` / `plaibook review`, and `ansible-playbook review.yml` for AAP.
- **[GitHub review check](github-review-check.md)**: the `plaibook review` check that runs after the other CI checks pass.
- **[Architecture](architecture.md)**: how the pipeline is built: the review role, domain-specific steering, and the independent verification pass.
- **[Reference](reference.md)**: every variable that controls a run.
- **[Dependency map](dependency-map.md)**: collection SHAs, Python SDK pins, the OpenShell gateway, and the review-workflow SHAs. Update the row in the same commit as the pin.
- **[Execution environment](execution-environment.md)**: `ghcr.io/aknochow/plaibook-ee` and the `ansible-builder build` command that produces it.

## Why "deterministic" matters here

The only genuinely non-deterministic parts of a run are the live model
calls themselves (the two review lenses, the exploration pass, and the
independent verify pass). Everything else (dedup, scoring, the
self-refuted-finding filter, persistence) is plain, testable Python and
Jinja with no model involved. Findings and scores can vary slightly
between two runs of the same diff; the *mechanism* that turns findings
into a verdict never does.
