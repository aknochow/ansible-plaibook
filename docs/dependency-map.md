---
type: Reference
title: Dependency map
description: The pins between plaibook, the provider collections, the Python SDKs, and OpenShell. Update the row in the same commit as the pin.
tags: [dependencies, pins, collections]
status: stable
---

# Dependency map

This file is the source of truth for how plaibook pins the provider
collections and their Python SDKs. A separate checkout of one of those
collections is not a pin. `plai review` installs the commit named in
[`collections-requirements.yml`](../collections-requirements.yml).

When a pin changes, update the matching row in the same commit. Do not
copy this matrix into another repository. Link here. Two copies drift,
and a drifted pin is how a review starts calling a module argument that
no longer exists.

`scripts/test_dependency_map.py` fails when a collection SHA, a hashed
SDK version, or a review-workflow pin is missing from this file.

## Projects

| Project | Repository | Role |
|---|---|---|
| plaibook | [ansible-plaibook](https://github.com/aknochow/ansible-plaibook) | The review playbook and the `plai` / `plaibook` package. It consumes the collections below. They do not depend on it. |
| aknochow.claude | [ansible-claude](https://github.com/aknochow/ansible-claude) | Claude module. Pins the Anthropic and Claude Agent SDKs. |
| aknochow.gemini | [ansible-gemini](https://github.com/aknochow/ansible-gemini) | Gemini module. Pins `google-genai`. |
| aknochow.openai | [ansible-openai](https://github.com/aknochow/ansible-openai) | OpenAI-compatible chat module. Pins the `openai` SDK. |
| aknochow.cursor | [ansible-cursor](https://github.com/aknochow/ansible-cursor) | Cursor agent module. Pins `cursor-sdk`. |
| aknochow.openshell | [ansible-openshell](https://github.com/aknochow/ansible-openshell) | Sandbox lifecycle and the SSH proxy. Pins the `openshell` Python SDK. |

The provider collections do not import each other. Plaibook composes
them with ordinary Ansible primitives. OpenShell does not import a
provider collection, and a provider collection does not import OpenShell.

## Rules

1. Change collection code in that collection's repository. After the
   merge, move only that collection's SHA in
   `collections-requirements.yml` to the merge commit. The SHA has to
   be an ancestor of the collection's default branch. Galaxy clones
   the default branch, then checks out the SHA.
2. `scripts/bump_collection_pins.py` and
   `.github/workflows/bump-collection-pins.yml` float aknochow
   collection SHAs to the default branch. They leave
   ansible-collections release SHAs on the tagged commit.
3. A Python SDK that plaibook installs at runtime comes from
   `plaibook/hashed/*-requirements.txt` (`pip install --require-hashes`).
   The exact version has to satisfy the range in the collection
   `requirements.txt` at the SHA in rule 1. Regenerate with
   `./scripts/compile-hashed-sdks.sh` after editing the matching
   `plaibook/hashed/*.in` file.
4. The execution environment installs
   `execution-environment-requirements.txt` with
   `pip install --require-hashes`. Direct pins are
   `execution-environment-requirements.in`. That file is not
   `plaibook/hashed`. Rebuild it with the `uv pip compile` command in
   its header when a pin changes. An image pin has to satisfy the
   collection range at the SHA in rule 1.
5. OpenShell is three pins. The Python SDK, the gateway installer, and
   the sandbox image move separately. Matching the SDK to the gateway's
   major version is required. Copying one version number onto the
   other two is not.
6. The GitHub review check is two SHAs. `pull_request_target` reads
   the caller from the default branch. Update the inner checkout first,
   merge that, then point the caller at that merge commit. Do not pass
   `@main`. See [GitHub plaibook review check](github-review-check.md).
7. Sibling collection CI calls
   `collection-ci.yml@main`. That ref floats. The review check must
   not.

## Collection git pins

Recorded in `collections-requirements.yml`. `version` is a full commit
SHA.

| Id | Collection | SHA | Which change |
|---|---|---|---|
| `collection.openshell` | ansible-openshell | `09cd2f193a77b3d59b263c62224e23fe364fa6e8` | ansible-openshell #38. Commit standards on main. SDK range is still `openshell>=0.1.3,<0.2` from #36. |
| `collection.claude` | ansible-claude | `dfc061b6bf0bf0c1462435ef1c1f106d830a52b0` | ansible-claude #34. Commit standards on main. SDK pins are still `anthropic` 1.11.0 and `claude-agent-sdk` 0.2.163 from #30. |
| `collection.gemini` | ansible-gemini | `016d7e2e5eceb2052304c740ee6a4abf44ca48ce` | ansible-gemini #28. Commit standards on main. `google-genai` 2.27.0 is still the pin from #22. |
| `collection.openai` | ansible-openai | `3657a671018dfda9f18328d2b0c179038f100797` | ansible-openai #30. Commit standards on main. `openai` 3.23.0 is still the pin from #25. |
| `collection.cursor` | ansible-cursor | `b4d2672761296e7338e801cccfd7ac20b08e564d` | ansible-cursor #39. Sends reasoning_effort for grok-4.7. The #38 start retry remains. `cursor-sdk` 1.0.35 and the serialized starts from #30 are unchanged. |
| `collection.community.general` | community.general | `049524674b13ad9782849c427266935c8ec61954` | Release 13.4.0. Do not float to the default branch. |
| `collection.kubernetes.core` | kubernetes.core | `0f472b53e2ee73e11b5f9067ab0826d76183c157` | Release 6.5.0. Do not float to the default branch. |
| `collection.ansible.posix` | ansible.posix | `e98d9a0756458be1ac710988498000973889075c` | Release 2.2.2. Do not float to the default branch. |

## Python SDK pins

The collection column is `requirements.txt` at the SHA above. The
hashed column is what `pip install --require-hashes` installs. The
hashed version has to fall inside the collection range. A newer
hashed version than the collection allows is a broken pin, not a
head start.

| Id | Package | Collection range | Plaibook range | Hashed install | Hashed file |
|---|---|---|---|---|---|
| `sdk.anthropic` | anthropic | `anthropic[vertex]>=0.84.0` | same floor, via the collection | `anthropic==1.11.0` | `plaibook/hashed/claude-requirements.txt` |
| `sdk.claude-agent` | claude-agent-sdk | `claude-agent-sdk>=0.2.144` | same floor, via the collection | `claude-agent-sdk==0.2.163` | `plaibook/hashed/claude-requirements.txt` |
| `sdk.gemini` | google-genai | `google-genai>=1.0.0` | same floor, via the collection | `google-genai==2.27.0` | `plaibook/hashed/gemini-requirements.txt` |
| `sdk.openai` | openai | `openai>=1.58.0` | same floor, via the collection | `openai==3.23.0` | `plaibook/hashed/openai-requirements.txt` |
| `sdk.cursor` | cursor-sdk | `cursor-sdk>=1.0.31,<2.0.0` | `cursor-sdk>=1.0.31,<2.0.0` in `pyproject.toml` | `cursor-sdk==1.0.35` | `plaibook/hashed/cursor-requirements.txt` and `sandbox-runtime-requirements.txt` |
| `sdk.openshell` | openshell | `openshell>=0.1.3,<0.2` | `openshell>=0.1.3,<0.2` in `pyproject.toml` and `plaibook/openshell_sdk.py` | `openshell==0.1.3` | `plaibook/hashed/openshell-requirements.txt` |

Direct inputs for the hashed files are `plaibook/hashed/*.in`.
`scripts/compile-hashed-sdks.sh` writes the `*-requirements.txt`
files. `uv.lock` pins the same OpenShell and cursor-sdk versions for
the plaibook package itself.

## OpenShell is three pins

| Id | What it is | Current pin | Where |
|---|---|---|---|
| `sdk.openshell` | Python package imported by the collection and by plaibook | `openshell==0.1.3`, range `openshell>=0.1.3,<0.2` | Hashed file, `plaibook/openshell_sdk.py`, and the collection at `collection.openshell`. |
| `openshell.gateway` | CLI and local gateway the review workflow starts | `OPENSHELL_VERSION=v0.1.2`. Installer source commit `6648bd0c290efbc41ba131ee9831ee45cd431f94`. sha256 `5c98a86a4b811c471b212219cb2a62d458244220ffa71ac8e3baf3700b17b871` | `.github/workflows/plai-review-run.yml` |
| `openshell.sandbox-image` | Guest image the review runs inside | `ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e` | `.github/workflows/plai-review-run.yml` (`SANDBOX_IMAGE`) |

The gateway and the Python SDK share a major version (`0.1`). They do
not share a patch number. Bumping the SDK to 0.1.3 does not by itself
move the installer or the image digest.

The image lock pins `openshell==0.1.3`. That matches the plaibook
hashed install and the collection range at `collection.openshell`.

## Execution environment pins

`execution-environment.yml` installs
`execution-environment-requirements.txt`. These are the direct pins in
`execution-environment-requirements.in`, next to the runtime hashed
install. They are allowed to differ. A watcher should treat a
difference as a bump to decide, not as the same pin.

| Package | Image pin | Runtime hashed install |
|---|---|---|
| anthropic | `anthropic[vertex]==1.13.0` | `anthropic==1.11.0` |
| claude-agent-sdk | `claude-agent-sdk==0.2.165` | `claude-agent-sdk==0.2.163` |
| google-genai | `google-genai==2.29.0` | `google-genai==2.27.0` |
| openai | `openai==3.28.0` | `openai==3.23.0` |
| cursor-sdk | `cursor-sdk==1.0.37` | `cursor-sdk==1.0.35` |
| openshell | `openshell==0.1.3` | `openshell==0.1.3` |

## Review workflow pins

| Id | What | SHA | Where |
|---|---|---|---|
| `review.caller` | `plai-review.yml` calls the reusable workflow | `528b023fbecfe3737f6ab0fe46fb26731b62a36c` | `.github/workflows/plai-review.yml` `uses:` |
| `review.checkout` | Both checkouts inside the reusable workflow | `b9fee9a904425934522a2d9f333c11f3701892bd` | `.github/workflows/plai-review-run.yml` `ref:` |

Move `review.checkout` first. Then set `review.caller` to the commit
that contains those checkout refs. The caller file on the default
branch is the one `pull_request_target` runs. Do not point the caller
at a commit whose workflow still checks out the previous tree.
`b9fee9a904425934522a2d9f333c11f3701892bd` is `main` after #156.
That tree includes the grok-4.7 pricing from #166.
`review.caller` is `528b023fbecfe3737f6ab0fe46fb26731b62a36c`.
That commit contains these checkout refs, so the called workflow
checks out `b9fee9a`. It is not `b9fee9a` itself: that tree's
workflow file still checks out `73889d7`.

## CI edges

| Id | From | To | Ref |
|---|---|---|---|
| `ci.collection` | ansible-claude, ansible-gemini, ansible-openai, ansible-cursor, ansible-openshell | `ansible-plaibook/.github/workflows/collection-ci.yml` | `@main` (floats) |
| `ci.review` | those same repositories, when they call the review check | `review.caller` | full SHA, not `@main` |

## What to flag

A dependency watcher should open a bump when any of these is true:

- A hashed `==` version does not satisfy the collection range at that collection's SHA.
- An aknochow collection SHA is not an ancestor of the collection's default branch.
- An ansible-collections SHA no longer matches the release named in its comment.
- The OpenShell Python SDK and `openshell.gateway` are on different major versions.
- `review.caller` moved without `review.checkout`, or the caller is `@main`.
- An image pin in `execution-environment-requirements.in` does not satisfy the collection range, or it differs from the runtime hashed install for the same package and nobody has decided which one moves.
