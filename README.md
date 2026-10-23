# ansible-plaibook

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/aknochow/ansible-plaibook/badge)](https://scorecard.dev/viewer/?uri=github.com/aknochow/ansible-plaibook)

AI-powered code review, running as a deterministic Ansible playbook rather than a chat session. `plai review` sends independent lenses over a diff, scores whatever findings survive with plain code (a lens does not get to grade itself), and writes the report. Same command from a terminal, a git hook, or an AAP job template.

## Install

```bash
pipx install plaibook
plai review
plai update
```

**`pipx install plaibook`**, then **`plai review`**. **`plai update`** upgrades the install that is running. Homebrew and Debian refuse `pip install` into the system Python ([PEP 668](https://peps.python.org/pep-0668/)); inside a virtualenv you already manage, `pip install plaibook` is the same install. The name on PyPI is `plaibook`. `plai` is taken, and `ansible-plaibook` is this repo. `plai` and `plaibook` share one entry point.

`plai review` with no arguments reviews `HEAD` here. `plai review org/repo/123` is a pull request or merge request. The wheel vendors `review.yml`, and the first run installs collections from GitHub into `~/.cache/ansible-plaibook/collections` (never `~/.ansible`, so a leftover symlink from a sibling checkout cannot break install). The CLI notes are in [`plaibook/README.md`](plaibook/README.md). Job templates still call `ansible-playbook review.yml`. Python 3.10, the macOS `/tmp` trap, commit review, and how to read the output are in [Getting started](docs/getting-started.md). Contributors use `uv sync --extra dev` — [CONTRIBUTING](CONTRIBUTING.md).

## Documentation

- [Getting started](docs/getting-started.md) — first review, commit mode, and the CLI output
- [Architecture](docs/architecture.md) — the review role, the five stages, and verification
- [Why Ansible](docs/why-ansible.md) — job templates, scoring, and the Ansible tools that come along
- [Sandboxes and LLM calls](docs/sandbox-and-agent-safety.md) — model calls stay on the controller
- [GitHub review check](docs/github-review-check.md) — the pull-request check that runs `plai review`
- [Variable reference](docs/reference.md) — every variable that changes a run
- [Dependency map](docs/dependency-map.md) — collection SHAs, SDK pins, and the OpenShell gateway
- [Execution environment](docs/execution-environment.md) — `ghcr.io/aknochow/plaibook-ee`
- [Release](docs/release.md) — version bump, GitHub Release, PyPI upload
- [Gotchas](docs/gotchas.md) — Vertex structured output, sandbox egress, and the gateway URL

## Dependencies

Plaibook pins provider collections, Python SDKs, the OpenShell gateway, and the review workflow in different files. Those numbers are not interchangeable. The map, and the order a bump has to follow, is [docs/dependency-map.md](docs/dependency-map.md).

## Running it in AAP

`execution-environment.yml` builds `ghcr.io/aknochow/plaibook-ee`, a
multi-arch (linux/amd64 and linux/arm64) execution environment. It
carries every collection pinned in `collections-requirements.yml`, the
Python dependencies those collections need, and `gh`/`glab` for PR/MR
API access. A pin bump on `main` rebuilds the image. This playbook has
already run as a real AAP Job Template against real MRs.

Use a version tag for AAP Job Templates. `:<version>` matches a
plaibook release (`:0.1.28` is the image for tag `v0.1.28`). `:main`
and `:latest` follow `main` and can be ahead of the newest PyPI
release.

OpenShell `ssh_proxy.py` and certs are not in the image. Supply them
at job-run time if a review needs them.

Verify a published tag before using it:

```bash
cosign verify --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity-regexp '^https://github.com/aknochow/ansible-plaibook/\.github/workflows/ee-publish\.yml@refs/(heads/main|tags/v.+)$' ghcr.io/aknochow/plaibook-ee:0.1.28
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
