---
type: Guide
title: AAP review service and GitHub check
description: Launch one plaibook review per target on Automation Controller, and require the GitHub check that waits for the other CI.
tags: [aap, github-actions, review]
status: stable
---

# AAP review service and GitHub check

`review.yml` is the job-template playbook. A caller launches **one
controller job per target** with `ansible.controller.job_launch`. Local
`plai review` stays a single review. `post_results` stays `false`. The
GitHub Actions workflow posts the pull-request review with its own
token after it reads the job artifact.

## Variables

| Variable | Environment | Purpose |
|---|---|---|
| `plaibook_controller_host` | `CONTROLLER_HOST` or `AAP_CONTROLLER_HOST` | Controller hostname, no scheme and no token. Example: `aap.example.com`. |
| `plaibook_controller_token` | `CONTROLLER_OAUTH_TOKEN` or `AAP_CONTROLLER_TOKEN` | OAuth token that can launch the template and read that job. Never commit it. The playbooks check that it is non-empty and do not print it. |
| `plaibook_review_job_template` | `PLAIBOOK_REVIEW_JOB_TEMPLATE` | Default `plaibook-review`. |
| `plaibook_controller_organization` | `PLAIBOOK_CONTROLLER_ORGANIZATION` | Default `Default`. |
| `plaibook_ee_image` | | Default `quay.io/aknochow/plaibook-ee:latest`. |
| `plaibook_launch_user` | | Optional. Receives **Execute** on `plaibook-review` only. |

TLS verification stays on (`validate_certs: true`). Do not point these
playbooks at a host with a token in the URL.

GitHub Actions uses the repository **variable** `AAP_CONTROLLER_HOST`
and the repository **secret** `AAP_CONTROLLER_TOKEN`. If either is
missing, the check **plaibook review** fails. It does not skip and it
does not install plaibook to run `plai review` on the runner.

## Define the service

From a checkout, with `ansible.controller` available (see the install
script below):

```bash
export CONTROLLER_HOST=aap.example.com
export CONTROLLER_OAUTH_TOKEN=...
ansible-playbook controller/define_review_service.yml
```

That creates:

- inventory `plaibook-localhost` with `localhost` (`ansible_connection: local`)
- execution environment `plaibook-ee`
- project `plaibook` tracking `https://github.com/aknochow/ansible-plaibook.git` branch `main`
- credential type `plaibook-github-token`, which injects `GH_TOKEN` and `GITHUB_TOKEN` from a controller credential
- job template `plaibook-review`, playbook `review.yml`, `ask_variables_on_launch: true`, template extra var `post_results: false`

Create the GitHub credential in the controller UI (the secret never
goes in git), name it, and re-run with
`-e plaibook_github_credential_name=that-name` so the template uses it.
The job also needs whatever provider credential the lenses use
(Anthropic, Gemini, OpenAI, or Cursor). Those stay controller
credentials.

Attach them to the template. The launch user does not need Credential
Admin. Grant only:

```yaml
role: execute
users:
  - plaibook-ci
job_templates:
  - plaibook-review
```

`controller/define_review_service.yml` does that when
`plaibook_launch_user` is set. Execute on this template lets that user
launch it and read jobs they started. It does not grant organization
admin, project admin, or credential admin.

## Launch one job

```bash
ansible-playbook controller/launch_review.yml \
  -e plaibook_review_target=org/repo/123
```

The launch extra vars set `review_type: pr`, `post_results: false`, and
`review_require_ci_passing: false`. The last one matters for the GitHub
gate: other checks may already be red, and this review is a separate
check. It does not mark those checks successful.

Set `CONTROLLER_OPTIONAL_API_URLPATTERN_PREFIX=/api/controller/` for the
platform gateway (the playbooks do this by default). Standalone AWX
uses `/api/`.

## What the job returns

Each run still writes `~/.cache/ansible-plaibook/last_run.<run_id>.json`.
It also writes `ci_result.<run_id>.json` and publishes the same document
on the job as `set_stats` artifact `plaibook_ci`. Callers read
`artifacts.plaibook_ci` from `GET /api/controller/v2/jobs/<id>/`. There
is no shared `ci_result.json`.

The document has `status`, `run_id`, `last_run_file`, `ci_result_file`,
and one entry per target with `verdict`, `score`, and `comments`. A
comment whose finding has a concrete replacement includes a GitHub
`suggestion` block. Prose fixes stay ordinary inline comments. Refuted
findings are omitted.

Passing verdict is `READY_FOR_HUMAN_REVIEW`. `NEEDS_CHANGES`,
`SKIPPED`, a failed job, or an empty artifact are failures of the
GitHub check.

## Install ansible.controller

`ansible.controller` is the collection AAP ships (Automation Hub). It
is not on public Galaxy. Public Galaxy publishes the same modules as
`awx.awx` 24.6.1 (`controller/requirements.yml`).

```bash
bash scripts/install-ansible-controller.sh
```

The script installs that pin and exposes `ansible.controller.job_launch`
from it, with `_COLLECTION_TYPE=controller` so requests use
`/api/controller/v2/`. If `ansible.controller` is already installed, the
script leaves it alone. The playbooks call `ansible.controller`, not a
private rewrite of the launch API.

## Official MCP server

AAP 2.6 and later ship **MCP server for Red Hat Ansible Automation
Platform**. It is not a server this repo implements. Job management can
list and launch job templates when an administrator deployed the MCP
server with read-write access (`mcp_allow_write_operations=true`). The
default read-only server cannot launch a job. The token's RBAC still
applies: Execute on `plaibook-review`, nothing broader.

Client configuration for the job-management toolset, from the AAP 2.6
and 2.7 product docs (containerized installs also use port `8448`; an
operator install uses the MCP route on the platform URL):

```json
{
  "mcpServers": {
    "aap-mcp-job-mgmt": {
      "type": "http",
      "url": "https://aap.example.com/job_management/mcp",
      "headers": {
        "Authorization": "Bearer ${env:AAP_MCP_TOKEN}"
      }
    }
  }
}
```

`AAP_MCP_TOKEN` is an AAP personal access token or service-account
token for the launch user. The discover endpoint is
`https://aap.example.com/discover/mcp` under the server name `aap-mcp`.
The launch tool in the job-management toolset is
`controller.job_templates_launch_create`. Listing is
`controller.job_templates_list`. Job status and stdout are
`controller.jobs_retrieve` and `controller.jobs_stdout_retrieve`.

If that MCP server is not deployed, or it is still read-only,
`ansible.controller.job_launch` is the path that starts the template.

## GitHub check

Check name to require in branch protection: **`plaibook review`**.

Re-run keyword: a pull-request comment that **starts with** `/plai-review`.
Comments from bots, and from users who are not OWNER, MEMBER, or
COLLABORATOR, are ignored. `workflow_dispatch` asks for the pull
request number.

The workflow does not start the review in parallel with the other
checks. It runs on `workflow_run` after CI, CodeQL, or OpenSSF
Scorecard complete, then polls until every required workflow run for
that commit has `status: completed` and no other check is still
`queued` or `in_progress`. A missing required workflow is not treated
as done. Failing checks stay failed. This workflow never updates them.

The check run is created only after that wait. Its conclusion is
`success` only when the artifact status is `ok` and every target
verdict is `READY_FOR_HUMAN_REVIEW`. Anything else, including "the
controller was not configured", is `failure`.

Inline comments use the fingerprint
`<!-- plaibook-finding:... -->`. A later run updates that comment
instead of posting a second copy. The summary review is posted once
(`<!-- plaibook-review-summary -->`). Later runs refresh the inline
comments and the check run; they do not file a second summary review.

Callers in other repositories must pin the reusable workflow to a full
commit SHA:

```yaml
uses: aknochow/ansible-plaibook/.github/workflows/plai-review-reusable.yml@<40-character-sha>
secrets:
  AAP_CONTROLLER_TOKEN: ${{ secrets.AAP_CONTROLLER_TOKEN }}
```

Do not pass that secret to a `uses:` ref of `@main` or a floating tag.
Actions in the reusable workflow are pinned by commit SHA.

This repo does not change branch protection. After the workflow is on
the default branch, require the check name `plaibook review` yourself.
OpenSSF Scorecard's Branch-Protection check only moves if that
requirement is actually on, and this change does not measure a score.
Token-Permissions stays at an empty top-level `permissions` block, with
`pull-requests: write`, `checks: write`, `contents: read`,
`actions: read`, and `statuses: read` on the job only.
Pinned-Dependencies is the SHA pins above, not a claimed score change.
