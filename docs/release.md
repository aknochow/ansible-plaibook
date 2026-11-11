---
type: Guide
title: Release
description: How a plaibook version becomes a GitHub Release, a PyPI upload, and an execution-environment image.
tags: [release, pypi, execution-environment]
status: stable
---

# Release

The package version stays in four places: `pyproject.toml`,
`plaibook/__init__.py`,
`.agents/skills/ansible-plaibook-review/SKILL.md`, and the
`ghcr.io/aknochow/plaibook-ee:<version>` example in the README.
`tests/test_version_alignment.yml` fails when those strings disagree.

## Bump

Run **Open a release pull request** (`release-bump.yml`) from `main`.
The input is `patch`, `minor`, `major`, or an explicit `X.Y.Z`.
The workflow writes that version, refreshes `uv.lock`, runs the
alignment playbook, and opens `chore/release-<version>`. It does not tag.

The release GitHub App opens the pull request. The job token can push
the branch, and this repository does not allow that token to create
pull requests. CI runs on the pull request. The alignment playbook
already ran in the bump job.

## Cut

Merging the release pull request is the release. The push to `main`
changes `pyproject.toml`, which starts **Cut a GitHub release**
(`release-cut.yml`). It reads the version at the pushed commit. If
`v<version>` already exists, the push was not a release and the job
stops without doing anything. Otherwise it builds the sdist and the wheel
from it, runs `scripts/check_dist.py`, and creates the tag plus a
non-prerelease Release on that commit. A package that cannot build never
gets a tag.

The workflow can also be run by hand from `main`. Run that way, an
existing tag fails the job.

The job uses the `release` environment. `RELEASE_GITHUB_APP_ID` and
`RELEASE_GITHUB_APP_PRIVATE_KEY` must be set there. The workflow
fails if either secret is unset. It does not use `GITHUB_TOKEN`,
because a release created with that token does not start
`publish.yml` or `ee-publish.yml`.

## Publish

`publish.yml` runs on `release: published`. It skips prereleases,
requires the tag to be `v` plus `pyproject.toml`'s version, requires the
commit to be on `main`, runs the same dist check, and uploads with PyPI
trusted publishing from the `pypi` environment. That environment has a
`v*` tag rule and no required reviewer, so the upload needs no approval.
`ee-publish.yml` publishes `ghcr.io/aknochow/plaibook-ee:<version>`
from the same release.

So a release is two steps: run **Open a release pull request**, then
merge the pull request it opens.

Pinning the review Action to the new tag is a separate pull request
after the tag exists. The release workflows do not change that pin.
