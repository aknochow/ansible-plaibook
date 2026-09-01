# Contributing to ansible-plaibook

## Running Tests Locally

This repository uses `uv` for reproducible environment management with a pinned lockfile (`uv.lock`). All unit tests and offline playbook tests are deterministic and require no API keys or live credentials.

### Setup and Sync:
```bash
uv sync --extra dev
uv run ansible-galaxy collection install ansible.posix
```

### Running Tests:
```bash
uv run pytest                                      # Python unit test suite (action plugins, modules, filters, scripts)
uv run ./scripts/run_playbook_tests.sh             # Offline Ansible playbook test suite
uv run ansible-playbook review.yml --syntax-check  # Playbook syntax check
```

## Commit Standards

- Sign off all commits (`git commit -s`).
- Include AI assistance attribution via trailer when applicable:
  `Assisted-by: Claude (<model-id>)` (never `Co-Authored-By:`).

## Before submitting a PR: avoiding sensitive/internal info leaks

This is a public repo. Nothing here catches every category of leak
automatically. Verify these yourself before pushing:

- **Credentials, tokens, API keys**: covered by `ai-guardian`'s
  `secret_scanning` (gitleaks + built-in patterns), which runs as part
  of every review (`roles/review/tasks/guardian_scan.yml`). If you have
  `ai-guardian` installed locally, `ai-guardian scan --diff` before
  pushing catches most of this category automatically.
- **Internal hostnames, project IDs, tool/service names**: **NOT**
  covered by `ai-guardian`. A hostname or GCP project ID isn't a
  "secret" in gitleaks' pattern sense, so it won't fire that scan even
  though it's still information you probably don't want to publish
  (an internal-only tool name, a real cloud project ID, a
  company-internal cluster hostname). This is a real, verified gap,
  checked directly against `ai-guardian`'s own CLI (`patterns`,
  `config`, `scan --config`), not assumed: it has no user-extensible
  custom-pattern mechanism today.
- **Your own org's specific deny-list**: use
  `review_neutralization_org_patterns` (see
  `action_plugins/check_neutralization_references.py`, wired through
  `roles/review/tasks/briefing.yml`). It ships with **no default
  patterns on purpose**. An empty list is the only honest default for
  a tool with no fixed home org. Set your own real internal strings in
  a **local, gitignored** vars file (e.g. `host_vars/localhost.yml`,
  already gitignored, see its own header comment), never in a
  committed default. Once set, `review.yml`/`review_type=commit`
  reviews of this repo will flag a diff that claims to have
  "neutralized"/"config-driven"-ed a hardcoded reference but actually
  left one of your configured patterns behind.

This checklist exists so a hardcoded internal reference is a mistake
this project catches once, not a recurring one.
