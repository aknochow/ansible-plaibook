# ansible-plaibook

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/aknochow/ansible-plaibook/badge)](https://scorecard.dev/viewer/?uri=github.com/aknochow/ansible-plaibook)

AI-powered code review, running as an unattended Ansible playbook rather than a chat. `plai review` puts independent lenses on a diff, then scores the findings that are still standing. The score comes from that list, not from the model's own grade. Same command from a terminal, a git hook, or an AAP job template.

```bash
pipx install plaibook
plai review
```

No arguments reviews `HEAD` in the current directory. `plai review org/repo/123` is a pull request or merge request. `plai update` upgrades the install that just ran.

The PyPI name is `plaibook`. `plai` was already taken, so the two commands are one program. Homebrew and Debian will not let you `pip install` into the system Python ([PEP 668](https://peps.python.org/pep-0668/)). A virtualenv you already manage can `pip install plaibook`. The first run puts the provider collections in `~/.cache/ansible-plaibook/collections` and does not touch `~/.ansible`.

A quiet run prints the verdict, the score, and each Critical or Major finding with `file:line` and a short why. Minor and nit stay as counts. The footer is the cost and this run's `last_run.<run_id>.json`. `-v` is the Ansible task list. `--json` is what another program should parse. Commit review, and how to read a run, are in [Getting started](docs/getting-started.md). The CLI's own notes are in [`plaibook/README.md`](plaibook/README.md).

## Collections

The model calls and the sandbox are separate collections. Plaibook pins them and composes them. They do not import each other.

| Collection | Repository | Role |
|---|---|---|
| `aknochow.claude` | [ansible-claude](https://github.com/aknochow/ansible-claude) | Claude, including the Agent SDK |
| `aknochow.gemini` | [ansible-gemini](https://github.com/aknochow/ansible-gemini) | Gemini |
| `aknochow.openai` | [ansible-openai](https://github.com/aknochow/ansible-openai) | OpenAI-compatible chat |
| `aknochow.cursor` | [ansible-cursor](https://github.com/aknochow/ansible-cursor) | Cursor agent |
| `aknochow.openshell` | [ansible-openshell](https://github.com/aknochow/ansible-openshell) | Sandbox lifecycle and the SSH proxy |

OpenShell does not know which provider is in use. A provider collection does not know OpenShell exists. Sandbox lifecycle is `delegate_to`. The review role never imports it. How those calls are split is in [Sandboxes and LLM calls](docs/sandbox-and-agent-safety.md).

The SHAs and SDK versions are not interchangeable. The map, and the order a bump has to follow, is [docs/dependency-map.md](docs/dependency-map.md).

## Ansible

`review.yml` already runs as an AAP job template against real merge requests. You get the RBAC, credentials, scheduling, and audit log you already use for everything else. The image and the tag to pin are under [Running it in AAP](#running-it-in-aap).

Every step is a named task. `ansible.posix.profile_tasks` is on, and each review records cost and tokens per call. Scores are recomputed from the findings that remain. A lens can talk itself out of one in prose. The number moves when the finding is actually dropped. There is no resident agent process. You pay for the calls a run makes.

The playbook uses the Ansible tools that already exist:

- `ansible-lint` runs in the pre-commit hook and in CI. The profile in [`.ansible-lint`](.ansible-lint) is `min`.
- `ansible-builder` builds the execution environment. See [Execution environment](docs/execution-environment.md).
- `ansible-galaxy` installs the collections from `collections-requirements.yml`.
- `ansible-navigator` runs the playbook in that image.
- AWX and Ansible Automation Platform run the job. `ansible-runner` is what the platform uses under the job.
- `ansible-doc` is how the collections document their modules.

The rest of the docs are the [index](docs/index.md). People changing this repo start at [CONTRIBUTING](CONTRIBUTING.md). How the stages fit together is [Architecture](docs/architecture.md).

## Running it in AAP

`execution-environment.yml` builds `ghcr.io/aknochow/plaibook-ee`, a
multi-arch (linux/amd64 and linux/arm64) execution environment. It
carries every collection pinned in `collections-requirements.yml`, the
Python dependencies those collections need, and `gh`/`glab` for PR/MR
API access. A pin bump on `main` rebuilds the image. This playbook has
already run as a real AAP Job Template against real MRs.

Use a version tag for AAP Job Templates. `:<version>` matches a
plaibook release (`:0.1.30` is the image for tag `v0.1.30`). `:main`
and `:latest` follow `main` and can be ahead of the newest PyPI
release.

OpenShell `ssh_proxy.py` and certs are not in the image. Supply them
at job-run time if a review needs them.

Verify a published tag before using it:

```bash
cosign verify --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity-regexp '^https://github.com/aknochow/ansible-plaibook/\.github/workflows/ee-publish\.yml@refs/(heads/main|tags/v.+)$' ghcr.io/aknochow/plaibook-ee:0.1.30
```

To build and host your own, from a clean checkout. `--squash all` is the same flag CI uses. The build process, the layer count, and why `new` does not fix a local Podman mount are in [Execution environment](docs/execution-environment.md).

```bash
ansible-builder build --squash all -t <your-registry>/plaibook-ee:latest -f execution-environment.yml
podman push <your-registry>/plaibook-ee:latest
```

Credentials (GitHub/GitLab tokens, OpenShell mTLS) are injected via
AAP credentials at job-run time, never baked into the image.

The image includes `aknochow.cursor`. `review.yml` loads that
collection only when `agent_family=cursor` starts the sidecar. Rebuild
when `collections-requirements.yml` or `execution-environment.yml`
changes. A Vertex-only parse failure is a playbook bug, not a missing
collection.
