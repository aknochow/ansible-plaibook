# Contributing to ansible-plaibook

## Running Tests Locally

This repository uses `uv` for reproducible environment management with a pinned lockfile (`uv.lock`). All unit tests and offline playbook tests are deterministic and require no API keys or live credentials.

### Setup and Sync:
```bash
uv sync --extra dev
# uv sync installs both console scripts: plaibook and plai (same main)
# First `plai review` installs collections from GitHub into
# ~/.cache/ansible-plaibook/collections (never ~/.ansible, never
# galaxy.ansible.com).
# Playbook tests still need collections on the isolated path:
uv run python scripts/ci-install-collections.py
```

If the project's `.venv` is not on PATH, invoke the CLI as
`uv run plai review …` from this checkout. That is the contributor
path, not the product. The product is `pip install plaibook` then
`plai review`. After the env is on PATH, the commands are
`plai review` (HEAD of cwd), `plai review org/repo/123`, and
`plai review --commit`.

### Running Tests:
```bash
uv run pytest                                      # Python unit test suite (action plugins, modules, filters, scripts)
uv run ./scripts/run_playbook_tests.sh             # Offline Ansible playbook test suite
uv run ansible-playbook review.yml --syntax-check  # Playbook syntax check
```

## Runtime SDK pins

Provider SDKs, the OpenShell SDK, the Python 3.11 sandbox-runtime's
plaibook dependencies, and the setuptools/wheel used to wheel this
plaibook build are installed from `plaibook/hashed/*-requirements.txt`
with `pip install --require-hashes`. The sandbox runtime installs
hashed setuptools, wheels this plaibook with `--no-build-isolation`,
then `pip install --require-hashes --no-deps` of that wheel. After
changing a pin in `plaibook/hashed/*.in`, regenerate:

```bash
./scripts/compile-hashed-sdks.sh
```

Do not pass version ranges to `pip install` in `plaibook/provider_sdk.py`
or `plaibook/openshell_sdk.py`.

## Collection pins

`collections-requirements.yml` is git sources at commit SHAs: aknochow
interface pins (`aknochow.cursor`, `aknochow.openai`, and the other
family collections) plus ansible-collections release commits.
Dependabot cannot update that file. After a sibling collection merge,
either bump the SHA by hand or run:

```bash
uv run python scripts/bump_collection_pins.py --write
```

`.github/workflows/bump-collection-pins.yml` does the same weekly, on
`workflow_dispatch`, and on `repository_dispatch` type
`collection-pin-bump`. GitHub Actions cannot subscribe to another
repository's events, and a GitHub webhook cannot POST
`repository_dispatch` itself (wrong payload). Sibling default-branch
pushes notify plaibook by calling
`.github/workflows/notify-plaibook-pin-bump.yml` with secret
`PLAIBOOK_DISPATCH_TOKEN` (fine-grained PAT or GitHub App installation
token with `actions:write` on `aknochow/ansible-plaibook`). ansible-collections
release SHAs stay on the tagged commit — the bumper does not float those
to default-branch HEAD.

To watch every family repo without a workflow in each one, point a
GitHub App (push events) at Event-Driven Ansible. The starter rulebook
is `eda/collection-pin-bump.yml`; it still needs an AAP event stream
URL, webhook HMAC secret, and a job template that runs the same bumper.


## Commit Standards

- Sign off all commits (`git commit -s`).
- Include AI assistance attribution via trailer when applicable:
  `Assisted-by: Provider (model)`, using the actual provider/tool and exact
  model that performed the work (for example,
  `Assisted-by: Codex (gpt-5.6-luna-xhigh)`). Never copy an attribution from
  another session or use `Co-Authored-By:`.

## Before submitting a PR: avoiding sensitive/internal info leaks

This is a public repo. Nothing here catches every category of leak
automatically. Verify these yourself before pushing:

- **Credentials, tokens, API keys**: covered by `ai-guardian`'s
  `secret_scanning` (gitleaks + built-in patterns), which runs as part
  of every review (`roles/review/tasks/guardian_scan.yml`) **when the
  binary is installed**. Unsandboxed reviews (`use_sandbox=false`) do
  not self-install; a missing binary is reported as
  `ai-guardian not installed` and the scan is skipped — treat that as
  "not scanned," not "nothing found." If you have `ai-guardian`
  installed locally, `ai-guardian scan --diff` before pushing catches
  most of this category automatically.
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
