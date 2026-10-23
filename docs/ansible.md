---
type: Concept
title: Ansible
description: The job template, the scoring, and the Ansible tools the playbook uses.
tags: [ansible, aap, design]
status: stable
---

# Ansible

`review.yml` already runs as an AAP job template against real merge requests. RBAC, credentials, scheduling, and the audit log are the ones already used for infrastructure jobs. The image is [Execution environment](execution-environment.md). The tag a job template should pin is in the repository [README](../README.md#running-it-in-aap).

Every step is a named task. `ansible.posix.profile_tasks` timing is on, and each review records cost and token usage per call. When something looks wrong, you read the task list.

`merge.yml` and `verify.yml` recompute every score on a 0–100 scale from the findings that remain. A lens can talk itself out of a finding in prose. The score changes when the finding is dropped from the list. See [Architecture](architecture.md).

Sandboxing is `delegate_to`. Script-touching tasks take their host from `review_delegate_host`. Sandbox lifecycle lives in the top-level playbook. The review role does not import OpenShell. See [Sandboxes and LLM calls](sandbox-and-agent-safety.md).

There is no resident agent service. The pipeline reports token totals and an estimate for every run. The amount depends on the provider, the model, cache hits, and how far exploration goes.

The playbook uses the existing `ansible-*` tools. This project does not reimplement them.

- **`ansible-lint`** runs from the pre-commit hook and from CI (`uv run ansible-lint --offline`). The profile in [`.ansible-lint`](../.ansible-lint) is `min`. Ansible can load the content. This is not the `production` profile Automation Platform uses for validated content.
- **`ansible-builder`** builds `execution-environment.yml` into `ghcr.io/aknochow/plaibook-ee`. See [Execution environment](execution-environment.md).
- **`ansible-galaxy`** installs the provider collections from `collections-requirements.yml`. See [Dependency map](dependency-map.md).
- **`ansible-navigator`** runs the playbook against that image. The command is in [Execution environment](execution-environment.md).
- **AWX and Ansible Automation Platform** are the job runner. `ansible-runner` is what the platform uses to execute a job.
- **`ansible-doc`** is how the provider collections document their modules. Operator docs for this playbook are these pages.
