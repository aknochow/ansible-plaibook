# ansible-plaibook

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/aknochow/ansible-plaibook/badge)](https://scorecard.dev/viewer/?uri=github.com/aknochow/ansible-plaibook)

AI-powered code review, running as an unattended Ansible playbook rather than a chat. `plai review` puts independent lenses on a diff, then scores the findings that are still standing. The score comes from that list, not from the model's own grade. Same command from a terminal, a git hook, or an AAP job template.

```bash
pipx install plaibook
plai review
```

No arguments reviews `HEAD` in the current directory. `plai review org/repo/123` is a pull request or merge request. `plai update` upgrades the install that just ran.

The PyPI name is `plaibook`. `plai` was already taken, so the two commands are one program. Homebrew and Debian will not let you `pip install` into the system Python ([PEP 668](https://peps.python.org/pep-0668/)). A virtualenv you already manage can `pip install plaibook`. The first run puts the provider collections in `~/.cache/ansible-plaibook/collections` and does not touch `~/.ansible`.

[Getting started](docs/getting-started.md) is the first review, commit mode, and how to read the output. [Architecture](docs/architecture.md) is how the playbook is built. [Why Ansible](docs/why-ansible.md) is the job-template argument. The rest of the pages are the [docs index](docs/index.md). Collection SHAs, SDK pins, and the OpenShell gateway are not interchangeable; the map and the order a bump has to follow are in [docs/dependency-map.md](docs/dependency-map.md). Contributors start at [CONTRIBUTING](CONTRIBUTING.md). The CLI's own notes are in [`plaibook/README.md`](plaibook/README.md).

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
