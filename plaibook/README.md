# plaibook CLI

This directory is the **CLI** (`plai` / `plaibook`). `review.yml` stays
at the [repo root](../README.md) in git; `pip install` copies that tree
into the wheel at `plaibook/share/`. Do not move the source playbook
here.

`plai review …` and `plaibook review …` are the same program. The pip/uv
distribution name is `plaibook` (not `plai`, taken on PyPI, and not
`ansible-plaibook`).

## Install

```bash
pipx install plaibook
plai review
```

That is the product. `plai review` does not change. Homebrew and Debian
refuse `pip install` into the system Python (PEP 668). Inside a
virtualenv you already manage, `pip install plaibook` is the same
install. `plai review` with no arguments reviews `HEAD` in
the current directory. Needs Python 3.10 or newer. First run prompts
for a provider (Cursor defaults to `gpt-5.6-luna` / `high`) and installs
collections from GitHub into `~/.cache/ansible-plaibook/collections`
(never galaxy.ansible.com). It never writes `~/.ansible/collections`, so
a leftover symlink from a sibling checkout cannot break install.

Until this version is on PyPI, the same wheel is `pip install .` from
this checkout (or clone the repo under `$HOME` and `pip install` that
path). Do not put the venv in `/tmp`: macOS XProtect blocks scripts
that land there and then execute. `plai review` uses
`~/.cache/ansible-plaibook/tmp` for clones and checklists, not `/tmp`.
Contributors use `uv sync --extra dev` — see
[CONTRIBUTING](../CONTRIBUTING.md), not the product.

Needs `git` on PATH (git-sourced collections) and network on first
collection install. Later runs reuse the cache until
`collections-requirements.yml` changes.

## Publishing to PyPI

The GitHub Action is [`.github/workflows/publish.yml`](../.github/workflows/publish.yml).
It uses Trusted Publishing (OIDC), not an API token. Before the first
release, on your PyPI account:

1. [Pending publisher](https://pypi.org/manage/account/publishing/):
   project `plaibook`, owner `aknochow`, repo `ansible-plaibook`,
   workflow `publish.yml`, environment `pypi`.
2. GitHub repo **Settings → Environments → New environment**: name
   `pypi`, URL `https://pypi.org/p/plaibook`. **Required reviewers** and
   a `v*` tag rule are the human publish gate (YAML cannot require
   them). Restrict deployments to those tags.
3. After this lands on `main`, publish a GitHub Release whose tag
   matches `pyproject.toml` `version` (first cut: `v0.1.0`) and whose
   commit is already on the default branch.
   `publish.yml` has no `workflow_dispatch`; only a non-prerelease
   published release whose tag is `v` plus that version, pointing at a
   commit on the default branch, can upload.

A pending publisher does **not** reserve the name until that first
successful upload. Cut the release soon after configuring it.
`plai` is taken on PyPI (unrelated); the distribution name is
`plaibook`.

## Commands

```bash
plai review
plai review org/repo/123
plai review --commit
plai review --commit --sha abc1234..def5678
plai review org/repo/123 --json
plai review org/repo/123 -f
plaibook review org/repo/123
```

`--yaml` is the YAML form of `--json`. `-v` passes `-v` to
`ansible-playbook` (task names). Combined with `--json` / `--yaml`,
that ansible output goes to stderr so stdout stays parseable. `-vv` /
`--debug` passes `-vv` (task names and module args) and skips the
spinner. `--full` (or `-v`) adds the findings.md report.
`-f` / `--force` re-runs lenses even when this commit was already
reviewed. A same-commit cache hit is labeled in the pretty review so a
$0.00 cost is not mistaken for a live run.
`--root` selects a local playbook checkout instead of this install's
bundled copy. First run with no operator config prompts for a
provider and writes `~/.config/ansible-plaibook/vars.yml`. `--provider
cursor` does the same non-interactively and defaults Cursor to
`gpt-5.6-luna` / `high`. PR/branch reviews skip nested OpenShell when
this process is already inside a sandbox (in-guest JWT). The SDK needs
Python 3.11+; a 3.10 `plai` switches to
`~/.cache/ansible-plaibook/sandbox-runtime` for that sandbox.
`--no-sandbox` stays on this interpreter (`--sandbox` to require it).

## Output sugar

Default stdout is a **readable review**: target, verdict, 0–100 scores,
Critical/Major with `file:line` + title + short why. Minor/nit stay as
counts. The footer is cost, the run-scoped `last_run.<run_id>.json`, and
the path to `findings.md`. A score line plus finding counts is not a
review. Quiet TTY waits show a spinner; the second line is the current
stage (setup, checkout, scan, lenses, merge, explore, verify, persist).

`--json` / `--yaml` print the structured last_run document plus
summary fields (what agents parse). The CLI does not rescore. It does
not dump the full report markdown unless `--full` or `-v`.

## Wheel vs later FQCN

This CLI still shells out to `ansible-playbook review.yml` (bundled in
the wheel, or a checkout). It does not yet run
`ansible-playbook aknochow.plaibook.review`.

**Later** (not this PR; this directory becomes its own repo):

`plai review …` == `ansible-playbook aknochow.plaibook.review …`

That FQCN does not work yet. Do not pretend it does. AAP /
execution-environment jobs keep calling `ansible-playbook review.yml`.

## Docs

- Repo overview: [../README.md](../README.md)
- First review: [../docs/getting-started.md](../docs/getting-started.md)
- Skill: [../.claude/skills/ansible-plaibook-review/SKILL.md](../.claude/skills/ansible-plaibook-review/SKILL.md)
