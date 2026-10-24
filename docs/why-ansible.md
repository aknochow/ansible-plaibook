---
type: Concept
title: Why Ansible
description: Why the review pipeline is an Ansible playbook and role, and which existing Ansible tools it uses.
tags: [ansible, aap, design]
status: stable
---

# Why Ansible

## The review pipeline

- **It ships as an AAP job template.** `review.yml` already runs as a job template against real merge requests. RBAC, credential management, scheduling, and audit logging are the ones the team already uses for infrastructure automation. See [Execution environment](execution-environment.md) for the image, and the [README](../README.md#running-it-in-aap) for the tag a job template should pin.
- **Every step is a named task.** `ansible.posix.profile_tasks` timing is wired in, and each review records cost and token usage per agent call. When something looks wrong, you read the task list.
- **Scores are never self-reported.** `merge.yml` and `verify.yml` recompute every score on a 0–100 scale from the surviving findings list. A lens can talk itself out of a finding in prose. The score changes only when the finding is dropped from the list. See [Architecture](architecture.md).
- **Sandboxing is `delegate_to`.** Every script-touching task takes its execution target from `review_delegate_host`. Sandbox lifecycle lives in the top-level playbook. The `review` role does not import OpenShell. See [Sandboxes and LLM calls](sandbox-and-agent-safety.md).
- **Cost follows the run.** There is no resident agent service. The pipeline reports token totals and an estimate for every run. The amount depends on provider, model, prompt-cache hits, and exploration depth.

## The tooling that comes with a playbook

The playbook and role use the existing `ansible-*` tools. This project does not reimplement them.

- **`ansible-lint`** runs from the pre-commit hook and from CI (`uv run ansible-lint --offline`). The profile in [`.ansible-lint`](../.ansible-lint) is `min`: Ansible can load the content. It is not the `production` profile Automation Platform uses for validated content.
- **`ansible-builder`** builds `execution-environment.yml` into the image published as `ghcr.io/aknochow/plaibook-ee`. See [Execution environment](execution-environment.md).
- **`ansible-galaxy`** installs the provider collections from `collections-requirements.yml`. See [Dependency map](dependency-map.md).
- **`ansible-navigator`** runs the playbook against that execution environment. The navigator command is in [Execution environment](execution-environment.md).
- **AWX and Ansible Automation Platform** are the job runner: RBAC, credential injection, scheduling, and audit logging. `ansible-runner` is what the platform uses to execute a job.
- **`ansible-doc`** is how the provider collections document their modules. Operator docs for this playbook are the pages in this directory.
