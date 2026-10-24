---
type: Concept
title: ansible-plaibook
description: Documentation for the plai review playbook, the CLI, and the execution environment.
tags: [overview, review-pipeline, ansible]
status: stable
---

# ansible-plaibook

Documentation for the review playbook and the `plai` / `plaibook` CLI. The repository [README](../README.md) is the short install page. These pages are the detail.

`plai review` and `plaibook review` wrap `review.yml` at the repository root. There is no `bug_pipeline.yml` on `main` and no `plai fix`. [Getting started](getting-started.md) is the first run.

## Guides

- [Getting started](getting-started.md). Install, run a review, and read the output.
- [GitHub review check](github-review-check.md). The pull-request check that runs `plai review` after the other checks pass.
- [Execution environment](execution-environment.md). `ghcr.io/aknochow/plaibook-ee` and `ansible-navigator`.
- [Release](release.md). Bump the version, cut a GitHub Release, and publish to PyPI.

## Concepts

- [Architecture](architecture.md). The review role, the five stages, domain steering, and verification.
- [Why Ansible](why-ansible.md). Job templates, scoring, and the Ansible tools the playbook uses.
- [Sandboxes and LLM calls](sandbox-and-agent-safety.md). Model calls stay on the controller. The sandbox runs untrusted commands.

## Reference

- [Variable reference](reference.md). Every variable that controls a run.
- [Dependency map](dependency-map.md). Collection SHAs, SDK pins, the OpenShell gateway, and the review-workflow SHAs. Update the row in the same commit as the pin.
- [Gotchas](gotchas.md). Vertex structured output, sandbox egress, and the gateway URL.
