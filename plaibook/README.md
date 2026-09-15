# plaibook CLI

This directory is the **CLI** (`plai` / `plaibook`), not the playbooks.
`review.yml` stays at the [repo root](../README.md). Do not move it here.

`plai review …` and `plaibook review …` are the same program. The pip/uv
distribution name is `plaibook` (not `plai`, taken on PyPI, and not
`ansible-plaibook`).

## Commands

```bash
plai review org/repo#123
plai review --commit
plai review org/repo#123 --json
plai review org/repo#123 -f
plaibook review org/repo#123
```

`--yaml` is the YAML form of `--json`. `-v` passes `-v` to
`ansible-playbook` (task names). Combined with `--json` / `--yaml`,
that ansible output goes to stderr so stdout stays parseable. `-vv` /
`--debug` passes `-vv` (task names and module args) and skips the
spinner. `--full` (or `-v`) adds the findings.md report.
`-f` / `--force` re-runs lenses even when this commit was already
reviewed. A same-commit cache hit is labeled in the pretty review so a
$0.00 cost is not mistaken for a live run.
`--root` / `PLAIBOOK_ROOT` select the ansible-plaibook checkout that
contains `review.yml`. First run with no operator config prompts for a
provider and writes `~/.config/ansible-plaibook/vars.yml`. `--provider
cursor` does the same non-interactively and defaults Cursor to
`gpt-5.6-luna` / `high`. PR/branch reviews skip nested OpenShell when
this process is already inside a sandbox (in-guest JWT). They fail
closed if that SDK is not importable from this interpreter
(`--no-sandbox` to review on the host, `--sandbox` to require it).

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

## v1 checkout vs later FQCN

v1 still locates `review.yml` in an ansible-plaibook checkout and
shells out to `ansible-playbook review.yml`. It does not vendor the
playbook tree into the wheel.

**Later** (not this PR; this directory becomes its own repo):

`plai review …` == `ansible-playbook aknochow.plaibook.review …`

That FQCN does not work yet. Do not pretend it does. AAP /
execution-environment jobs keep calling `ansible-playbook review.yml`.

## Install story

Honest `pip install plaibook` waits on plaibook-as-a-collection **and**
the provider wheels. The provider wheels are on `main` now;
plaibook-as-collection is not. Until then, install from an
ansible-plaibook checkout (`uv sync` / `pip install -e .`). If that
checkout's `.venv` is not on PATH, `uv run plai` is the contributor
invocation — see [CONTRIBUTING](../CONTRIBUTING.md), not the product.

## Docs

- Repo overview: [../README.md](../README.md)
- First review: [../docs/getting-started.md](../docs/getting-started.md)
- Skill: [../.claude/skills/ansible-plaibook-review/SKILL.md](../.claude/skills/ansible-plaibook-review/SKILL.md)
