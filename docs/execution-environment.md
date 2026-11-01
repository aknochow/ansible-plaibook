---
type: Guide
title: Execution environment
description: How ghcr.io/aknochow/plaibook-ee is built with ansible-builder and how to run review.yml in it.
tags: [execution-environment, ansible-builder, aap]
status: stable
---

# Execution environment

`ghcr.io/aknochow/plaibook-ee` is the container Automation Controller uses to run [`review.yml`](../review.yml). `plai` and `plaibook` are not in the image. They are host commands that shell out to `ansible-playbook review.yml` and print the summary. A job template, `ansible-navigator`, and `plai review --ee` run the playbook inside this image.

## Run it

Pin a release tag on a job template. A GitHub release `v0.1.26` publishes `:0.1.26`. `:main` and `:latest` follow `main` and can be ahead of the newest package on PyPI. `:latest` is a copy of `:main`, not of the newest release tag. A tag can be moved, so resolve it to a digest and run that digest. `--pull-policy always` fetches that digest instead of reusing a local image.

```bash
tag=0.1.26
podman pull "ghcr.io/aknochow/plaibook-ee:${tag}"
digest="$(podman image inspect --format '{{.Digest}}' "ghcr.io/aknochow/plaibook-ee:${tag}")"
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity "https://github.com/aknochow/ansible-plaibook/.github/workflows/ee-publish.yml@refs/tags/v${tag}" \
  "ghcr.io/aknochow/plaibook-ee@${digest}" &&
ansible-navigator run review.yml \
  --eei "ghcr.io/aknochow/plaibook-ee@${digest}" \
  --mode stdout \
  --pull-policy always
```

`cosign verify` has to succeed before navigator runs. The identity is this release's workflow ref, `refs/tags/v0.1.26` for tag `0.1.26`, not a signature minted for `main` or for another release.

Pass the same extra vars you would pass to `ansible-playbook` (`-e review_type=commit`, and so on). GitHub, GitLab, and OpenShell credentials are job inputs. They are not baked into the image. `ssh_proxy.py` and OpenShell certs are not in the image either.

The package starts private. Controller needs a `ghcr.io` registry credential until the package is made public. This workflow cannot change that setting.

## Run a local review in it

`plai review <target> --ee` runs one PR, MR, or branch review in one container, started with `ansible-navigator`. `plai` stays on the host. It needs `ansible-navigator` (`pipx install ansible-navigator`) and Podman or Docker.

```bash
plai review org/repo/123 --ee
plai review org/repo/123 --ee --ee-image "ghcr.io/aknochow/plaibook-ee@sha256:<digest>"
```

`--ee` is not a queue and is not needed for running reviews at the same time. Its purpose is to keep host credentials away from a review of untrusted content. A local commit review (`--commit`) is refused, because that review writes into your checkout. A local path as the branch target is refused, because it is not in the container. `--sandbox` and `claude_cli` are refused too. The `claude` binary is not in the image, and OpenShell stays a separate choice.

### Which image

The default is `ghcr.io/aknochow/plaibook-ee:<plaibook version>`, the image the same release published. `PLAIBOOK_EE_IMAGE` or `--ee-image` replaces it. The image is pulled only when it is not already local.

For the default image, `plai` runs `cosign verify` against this release's workflow identity and starts the verified digest, so moving the tag changes nothing. `cosign` must be on `PATH`, and the run stops if verification fails. An image from `--ee-image` or `PLAIBOOK_EE_IMAGE` is your choice and is not verified, and `plai` says so when it runs. To verify one yourself, use the `cosign verify` command in [Run it](#run-it) and pass the digest to `--ee-image`.

### What the container can see

Collections and provider SDKs come from the image. `review_runtime_from_image` skips the per-run installs, no collection cache is mounted, and each container has its own home, so concurrent runs with different pins cannot share a collection path.

| Host path | Container path | Mode | Why |
|---|---|---|---|
| The playbook tree (a checkout's tracked files, or the package from a wheel) | `/plaibook/playbook` or `/plaibook/lib/plaibook` | read-only | The image does not contain the playbook or the `plaibook` package. A checkout's tracked files are copied into the run first, so `.git`, untracked files, and `host_vars/` are not visible and do not apply. |
| `~/.cache/ansible-plaibook/ee-runs/<run_id>/home` | same path | read-write | The container's `$HOME`, its clones, and its results. Deleted when the run ends. |
| `~/.cache/ansible-plaibook/ee-runs/<run_id>/runner` | `/runner` | read-write | `ansible-runner` metadata, including an env file with the values below. Deleted when the run ends. |
| `~/.cache/ansible-plaibook/ee-runs/<run_id>/passwd` | `/etc/passwd` | read-only | Names the host UID with the run home as its home. The image entrypoint would otherwise replace `$HOME`. |
| `~/reviews`, or `review_output_base` | same path | read-write | Prior rounds and this round's report. |
| `/etc/ssh/ssh_known_hosts`, when it exists | same path | read-only | `ansible-runner` mounts it. It holds public host keys. |

These are deliberately not mounted: `~/.ssh`, the SSH agent, `~/.cache/ansible-plaibook` itself, the directory you ran `plai` from, and any engine socket. A write to the host cache could change the code the next host review runs. `ansible-runner` always mounts `$HOME/.ssh`, so `plai` starts navigator with `HOME` set to an empty directory that links only the engine's own configuration.

`vars.yml` is copied into the run home without settings whose names look secret, at any depth. That filter goes by name only. Do not keep a secret there.

The environment is an allowlist. Navigator starts with only what it needs to find the engine, and the container gets the selected provider's variables, `ANSIBLE_REVIEW_*`, and the forge token for the target. `GH_TOKEN` comes from `gh auth token` on the host when neither GitHub variable is set. Values reach navigator through its environment and never appear on a command line. The container also runs as your UID with `--cap-drop=all` and `no-new-privileges`. SELinux labels are off, so that no host directory is relabeled, which removes SELinux from this container's confinement.

### What is not isolated

This step separates the review from the host. It does not separate the provider key from tool execution.

| Process | Provider key and forge token |
|---|---|
| `plai`, `ansible-navigator`, and the engine client on the host | Visible, in their environment |
| `ansible-playbook` in the container and the model modules | Visible |
| `git`, `gh`, `ai-guardian`, exploration searches, and `CHECKLIST.md` commands in the container | Visible. They run as the same UID, inherit that environment, and can read `/runner/artifacts/*/env.list`. |

The container has unrestricted network egress. A command that gets code execution from PR content can read the selected provider key and forge token and send them anywhere, and can edit `~/reviews`. It cannot reach the host's other keys, SSH keys, or files outside the table. `CHECKLIST.md` is read from the target ref and its commands pass an allowlist, and that is the control on this path. Use a narrowly scoped forge token. An OpenShell sandbox runs those commands away from the keys, and `--ee` does not replace that.

### Where results land

| Result | Host path |
|---|---|
| This run's summary | `~/.cache/ansible-plaibook/last_run.<run_id>.json` |
| Report and `summary.json` | `review_output_base` (`~/reviews/...`), including the run-scoped `summary.<id>.json` and `findings.<id>.md` |
| Ledger | One line appended to `~/.cache/ansible-plaibook/ledger/<host>.jsonl`, named for the host and not the container |

`last_run.json` is not updated by `--ee`. Read the run-scoped file. `plai` treats what the container wrote as untrusted. It refuses a last-run file that links out of the run or a target path outside the reviews directory, and copies only ledger lines carrying this run's ID.

A run tree left behind by a killed `plai` is removed by the next `--ee` run after 24 hours. It contains the env file until then, in a directory only you can read.

## Build command

CI and a local rebuild use the same command. ansible-builder 3.1.1 `build` writes the context and then runs Podman. [`--squash`](https://docs.ansible.com/projects/builder/en/latest/usage/#squash) is a Podman-only flag on that command:

| Value | Podman flag | What is left |
|---|---|---|
| `off` | none | Every base layer plus every layer this build adds. This is the default. |
| `new` | `--squash` | The base image's layers, plus one layer for everything this build added. |
| `all` | `--squash-all` | One layer. The base image's layers are included. |

```bash
ansible-builder build \
  --container-runtime podman \
  --squash all \
  --tag <your-registry>/plaibook-ee:latest \
  --file execution-environment.yml
```

CI adds `--context` so the context stays out of the workspace, and `--extra-build-cli-args "--platform linux/amd64"` (or `linux/arm64`) so each job builds one architecture. `--extra-build-cli-args` is appended to the `podman build` ansible-builder constructs. It is how a platform reaches Podman without a second build step.

`ansible-builder create` only writes `context/Containerfile`. The workflows used to stop there and call `podman build` themselves. `build` is that pair, and it is the command the definition file's header documents.

## Why `--squash all`

An unsquashed image built from this definition has 47 filesystem layers. `podman history` lists more rows than that, because `ARG`, `ENV`, `LABEL`, and `USER` do not become filesystem layers.

32 of the 47 are `registry.access.redhat.com/hi/python:3.12-builder`, pinned by digest in [`execution-environment.yml`](../execution-environment.yml). That image is the builder variant because the runtime `hi/python` image has no `/bin/sh`, and Ansible runs tasks through a shell. Hummingbird publishes the builder as many small layers (its history labels them `chunkah`). The other layers are the ones ansible-builder emits: one `COPY` or `RUN` each for the base packages, pip, Ansible, the collection install, the Python dependencies, `/runner`, `dumb-init`, `gh`, and `glab`.

`--squash new` keeps those 32 base layers and folds only our steps into one. The mount is still too large. On a Podman that mounts overlays with `metacopy=on`, `podman run` and `ansible-navigator` both fail while creating the overlay mount, before the playbook starts:

```text
creating overlay mount ... input/output error
```

`--squash all` folds the base and our steps into one layer. The image config stays: user `1000`, the execution-environment entrypoint, `dumb-init`, labels, and the working directory. A one-line change then republishes the whole image instead of a small extra layer. `podman history` no longer shows which `RUN` added a package.

There is no ansible-builder flag that squashes only the base and leaves our `COPY` and `RUN` lines as separate layers. That would be a separate Podman squash of the Hummingbird image, then a normal build on top of it.

## What the definition file does not do

ansible-builder 3.1.1 has no layer option inside `execution-environment.yml`. [`options`](https://docs.ansible.com/projects/builder/en/latest/definition/) sets the user, workdir, package manager, and entrypoint. It does not squash.

A trailing `RUN dnf clean all && rm -rf /tmp/*` adds a layer. Files deleted in a later layer stay in the earlier layer and are only hidden, so the image does not get smaller. A delete shrinks the published image when it is in the same `RUN` as the install, which the `gh` and `glab` steps do, or when `--squash all` drops the hidden files.

`PKGMGR_PRESERVE_CACHE` already defaults to empty, and ansible-builder already passes `--no-cache-dir` on the pip installs it generates. Setting those again does not remove a layer.

## What goes into the image

The base digest, `user: "1000"`, `package_manager_path: /usr/bin/dnf`, and `python_path: /usr/bin/python3` are what make ansible-builder 3.1.1 succeed on this Hummingbird image. The final stage is `USER 1000`. Do not install the `python3-pip` RPM: on this repo it depends on the `python3` RPM, which is Python 3.14 and replaces `/usr/bin/python3`.

Collections come from [`collections-requirements.yml`](../collections-requirements.yml). Python dependencies come from [`execution-environment-requirements.txt`](../execution-environment-requirements.txt) and are installed with `pip install --require-hashes`. The collection `requirements.txt` ranges are listed under `dependencies.exclude.python` so the introspect step cannot install those ranges beside the lockfile. Pins are recorded in the [dependency map](dependency-map.md).

`gh` 2.103.3 is the signed GitHub release RPM. The public key is [`execution-environment-gh-key.asc`](../execution-environment-gh-key.asc). `glab` 1.126.0 is GitLab's unsigned RPM. Both installs check a per-architecture SHA-256 before `rpm` runs. Both binaries are Go 1.27.3.

## CI

[`.github/workflows/ee.yml`](../.github/workflows/ee.yml) runs on pull requests. It builds `linux/amd64` on `ubuntu-latest` and `linux/arm64` on `ubuntu-24.04-arm`, smokes the image, and pushes nothing.

[`.github/workflows/ee-publish.yml`](../.github/workflows/ee-publish.yml) runs on a push to `main` that changes an image input, and on a published GitHub release. It uses the same `ansible-builder build --squash all` command, then pushes each architecture, signs it with cosign, attaches a Syft SBOM, and publishes a multi-arch manifest. A release tag `vX.Y.Z` publishes `:X.Y.Z`. A `main` push publishes `:main` and copies it to `:latest`.

The SBOM is taken from the docker-archive the job already saved for Trivy. Syft's `podman:` source needs a Podman API socket, and the GitHub runner does not expose one. `cosign login` uses the same token as `podman login`. Podman's credentials are not visible to cosign.
