---
type: Guide
title: Getting Started
description: Run your first review and read its output correctly.
tags: [quickstart, review, cli]
status: stable
---

# Getting Started

The short command is **`plai`**. The package and full command are
**`plaibook`**. They share one entry point.

```bash
pipx install plaibook
plai review
```

`plai review` is unchanged. Homebrew and Debian
refuse `pip install` into the system Python (PEP 668); `pipx` (or
`uv tool install plaibook`) is the install that puts `plai` on `PATH`.
Inside a virtualenv you already manage, `pip install plaibook` works.

Needs Python 3.10 or newer (3.10 stays on ansible-core 2.16; 3.11+
gets 2.18/2.19). OpenShell sandboxes need Python 3.11+. A 3.10 `plai review`
creates `~/.cache/ansible-plaibook/sandbox-runtime` from the first
`python3.11` (or newer) it finds, installs this plaibook build, the
OpenShell SDK, and the configured provider SDK (openai / anthropic /
google-genai) there, and continues with that interpreter. `--no-sandbox`
stays on the 3.10 interpreter. OpenShell sandbox *policy* does not need
provider API hosts for a default review: model calls stay on this
machine. Guest allow lists matter if you override `sandbox_policy`
(that replaces the gateway default, including network) or run
checklists that need egress. See
[`docs/sandbox-and-agent-safety.md`](sandbox-and-agent-safety.md#openshell-network-policy-vs-provider-apis).
`plai review` with no arguments reviews `HEAD` in the current
directory. The wheel vendors `review.yml`. First run clones collections
from GitHub into `~/.cache/ansible-plaibook/collections` (never
galaxy.ansible.com, never `~/.ansible`). See
[`plaibook/README.md`](../plaibook/README.md).
Until this version is on PyPI, `pip install .` from this checkout is
the same wheel. Use a virtualenv under your home directory, not `/tmp`:
macOS XProtect blocks scripts that appear in `/tmp` and then run.
`plai review` keeps its own scratch in `~/.cache/ansible-plaibook/tmp`
for the same reason. AAP / execution-environment jobs keep calling
`ansible-playbook review.yml` directly.

## Review a GitHub PR or GitLab MR

```bash
plai review org/repo/123
plaibook review org/repo/123
plai review org/repo/pull/123
plai review https://github.com/org/repo/pull/12
plai review gitlab:org/repo/34
```

These assume `plaibook` is installed (`pip install plaibook`, or
`pip install .` from this checkout). The CLI locates `review.yml` from
that install. `--root` points at a local checkout instead. cwd is left
alone, so you can review another repo without `cd`.

The first `plai review` with no operator config asks which provider to
use and writes `~/.config/ansible-plaibook/vars.yml`. Cursor defaults
to `gpt-5.6-luna` at effort `high`. `--provider cursor` saves that
without a prompt. Re-reviewing the same SHA is a $0 cache hit unless
you pass `-f` / `--force`.

AAP / execution-environment runs still invoke the playbook:

```bash
ansible-playbook review.yml -e review_targets_raw=org/repo/123
```

After the `PLAY RECAP`, the playbook prints a final review summary with
the PR/MR title, target, findings report path, and any failure or skip
reason. Local runs enable this through the checkout's `ansible.cfg`. In
AAP, add `plaibook_review_summary` to the Job Template's **Ansible
Callback Plugins** and make this repository's `callback_plugins/`
directory available as the callback plugin path.

`review_targets_raw` accepts a GitHub PR URL, a GitLab MR URL, or a bare
`org/repo/N` / `org/repo/pull/N` (GitHub) / `gitlab:org/repo/N` (GitLab)
identifier. Those bare forms are safe unquoted in bash (`#` is a comment,
`!` is history).
Pass several targets at once on the playbook path as a newline-separated
string, or use the JSON-list form:

```bash
ansible-playbook review.yml -e '{"review_targets": ["org/repo/1", "org/repo/2"]}'
```

## Review a single local commit: fast and cheap

```bash
plai review
plai review --commit
plaibook review --commit
plai review --commit --sha abc1234 --repo /path/to/repo
```

`plai review` with no PR/MR target is `--commit`. Both `--sha` and
`--repo` are optional (`--sha` defaults to `HEAD`, `--repo` to the
current directory). This mode skips the sandbox and the exploration
pass. It only sees the diff itself, not the surrounding codebase, so
it's fast and inexpensive, at the cost of missing anything that
requires reading a file outside the diff. A `NEEDS_CHANGES` verdict
with a real Critical/Major finding exits non-zero, on both `plai` and
`plaibook`.

## CLI output

Default stdout is a readable review, not ansible TASK spam and not
`last_run.json`. It prints the target, verdict, 0–100 scores,
Critical/Major findings with `file:line` + title + short why, minor/nit
as counts, then a footer (cost, run-scoped `last_run.<run_id>.json`,
path to `findings.md`). A `SKIPPED` verdict (CI failing on the PR head)
prints the reason and failing check names here, not only under `-v`.
Pass `-e review_require_ci_passing=false` to review anyway. A score line
plus finding counts is not a review.

`-v` passes `-v` to `ansible-playbook` (task names) and includes the
full findings.md report. `-vv` / `--debug` passes `-vv` so you see
task names and module args; there is no spinner. `--full` dumps that
report without the TASK wall.
`--json` and `--yaml` write the structured last_run + summary fields
on stdout (what agents parse). The CLI reads
`~/.cache/ansible-plaibook/last_run.<run_id>.json`; it does not scrape
playbook stdout or recompute scores.

## Reading the playbook artifacts

Every run writes one predictable file, overwritten each run:

```
~/.cache/ansible-plaibook/last_run.json
```

```json
{
  "targets": [
    {
      "target": "org/repo#123",
      "report": "<full rendered findings.md text>",
      "verdict": "READY_FOR_HUMAN_REVIEW | NEEDS_CHANGES",
      "score": 83.0,
      "summary_path": "/path/to/summary.json",
      "findings_path": "/path/to/findings.md"
    }
  ],
  "cost_usd": 0.1234,
  "started_at": "2026-09-08T13:46:41+00:00",
  "finished_at": "2026-09-08T13:54:32+00:00",
  "duration_seconds": 471,
  "phase_durations_seconds": {
    "guardian_scan": 0,
    "lenses": 265,
    "merge": 1,
    "explore": 84,
    "verify": 113,
    "persist": 2,
    "unattributed": 6
  },
  "total_input_tokens": 12345,
  "total_output_tokens": 6789,
  "agents_dispatched": 3
}
```

If more than one session might be reviewing at the same time, don't
trust this shared path. `plai review --json` already prints the
run-scoped copy. On the playbook path, read the run-scoped file
(printed as `RESULT_SUMMARY_RUN_SCOPED:`) instead, since concurrent
invocations race to overwrite the shared file.

Drill into a target's `summary_path` for the full structured
`summary.json`: verdict, per-lens scores, and every finding with its
file, line, severity, evidence, and verification status.

## Verdict rule

Any surviving Critical or Major finding forces `NEEDS_CHANGES`, no
confidence carve-out. Per-lens and overall scores are recomputed
deterministically from the surviving findings list itself, never read
directly from a lens's own self-reported score line, since a lens can
verbally retract a finding without recomputing the number that goes with
it.

## GitHub check

Pull requests can require the check `plai / review`. It runs `plai review` on the GitHub-hosted runner after the other checks on that commit have passed, then posts suggestion comments. It does not call an Automation Controller. See [github-review-check.md](github-review-check.md).

## Post the review back to the real PR/MR (opt-in)

Reviewing is safe to automate by default; posting is a write to shared
state and requires explicit opt-in:

```bash
plai review gitlab:org/repo/34 --post
# AAP / EE:
ansible-playbook review.yml -e review_targets_raw=gitlab:org/repo/34 -e post_results=true
```
