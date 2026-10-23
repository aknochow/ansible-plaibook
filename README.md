# ansible-plaibook

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/aknochow/ansible-plaibook/badge)](https://scorecard.dev/viewer/?uri=github.com/aknochow/ansible-plaibook)

AI-powered code review, as an Ansible playbook. `plai review` runs independent lenses over a diff, scores the findings in plain code, and writes a report. The same command runs from a terminal, a git hook, or an AAP job template.

## Install

```bash
pipx install plaibook
plai review
plai update
```

`plai` and `plaibook` are one program. The package name is `plaibook` (`plai` is taken on PyPI). Homebrew and Debian refuse `pip install` into the system Python ([PEP 668](https://peps.python.org/pep-0668/)). Inside a virtualenv you already manage, `pip install plaibook` is the same install.

`plai review` with no arguments reviews `HEAD` in the current directory. `plai review org/repo/123` reviews a pull request or merge request. The first run installs collections into `~/.cache/ansible-plaibook/collections`.

Install details, commit review, and how to read the output are in [Getting started](docs/getting-started.md). AAP job templates call `ansible-playbook review.yml`. Contributors use [CONTRIBUTING](CONTRIBUTING.md).

## Documentation

- [Getting started](docs/getting-started.md) covers install, the first review, and the CLI output.
- [Architecture](docs/architecture.md) covers the review role, the five stages, and verification.
- [Why Ansible](docs/why-ansible.md) covers job templates, scoring, and the Ansible tooling that comes with the playbook.
- [Sandboxes and LLM calls](docs/sandbox-and-agent-safety.md) covers where model calls run and where the sandbox runs.
- [GitHub review check](docs/github-review-check.md) covers the pull-request check that runs `plai review`.
- [Variable reference](docs/reference.md) covers every variable that changes a run.
- [Dependency map](docs/dependency-map.md) covers collection SHAs, SDK pins, and the OpenShell gateway.
- [Execution environment](docs/execution-environment.md) covers `ghcr.io/aknochow/plaibook-ee` and how to run `review.yml` in it.
- [Release](docs/release.md) covers the version bump, the GitHub Release, and the PyPI upload.
- [Gotchas](docs/gotchas.md) covers Vertex structured output, sandbox network policy, and the gateway URL.

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
