---
type: How-to
title: Execution environment
description: How ghcr.io/aknochow/plaibook-ee is built with ansible-builder and how to run review.yml in it.
tags: [execution-environment, ansible-builder, aap]
status: stable
---

# Execution environment

`ghcr.io/aknochow/plaibook-ee` is the container Automation Controller uses to run [`review.yml`](../review.yml). `plai` and `plaibook` are not in the image. They are host commands that shell out to `ansible-playbook review.yml` and print the summary. A job template, and `ansible-navigator`, run the playbook inside this image.

## Run it

Pin a release tag on a job template. A GitHub release `v0.1.26` publishes `:0.1.26`. `:main` and `:latest` follow `main` and can be ahead of the newest package on PyPI. `:latest` is a copy of `:main`, not of the newest release tag.

```bash
ansible-navigator run review.yml \
  --eei ghcr.io/aknochow/plaibook-ee:main \
  --mode stdout \
  --pull-policy missing
```

Pass the same extra vars you would pass to `ansible-playbook` (`-e review_type=commit`, and so on). GitHub, GitLab, and OpenShell credentials are job inputs. They are not baked into the image. `ssh_proxy.py` and OpenShell certs are not in the image either.

The package starts private. Controller needs a `ghcr.io` registry credential until the package is made public. This workflow cannot change that setting.

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

`gh` 2.102.0 is the signed GitHub release RPM. The public key is [`execution-environment-gh-key.asc`](../execution-environment-gh-key.asc). `glab` 1.122.0 is GitLab's unsigned RPM. Both installs check a per-architecture SHA-256 before `rpm` runs.

## CI

[`.github/workflows/ee.yml`](../.github/workflows/ee.yml) runs on pull requests. It builds `linux/amd64` on `ubuntu-latest` and `linux/arm64` on `ubuntu-24.04-arm`, smokes the image, and pushes nothing.

[`.github/workflows/ee-publish.yml`](../.github/workflows/ee-publish.yml) runs on a push to `main` that changes an image input, and on a published GitHub release. It uses the same `ansible-builder build --squash all` command, then pushes each architecture, signs it with cosign, attaches a Syft SBOM, and publishes a multi-arch manifest. A release tag `vX.Y.Z` publishes `:X.Y.Z`. A `main` push publishes `:main` and copies it to `:latest`.

The SBOM is taken from the docker-archive the job already saved for Trivy. Syft's `podman:` source needs a Podman API socket, and the GitHub runner does not expose one. `cosign login` uses the same token as `podman login`. Podman's credentials are not visible to cosign.
