---
type: Guide
title: Getting Started
description: Run your first review and read its output correctly.
tags: [quickstart, review, cli]
status: stable
---

# Getting Started

The recommended local entry point is **`plai review`**. The full command
is **`plaibook review`**; both names run the same program. This guide
starts with a fresh CLI install and first PR review. Details for other
install environments are in [Notes](#notes).

## Prerequisites

Have these ready before the first review:

- **Python 3.10 through 3.14** and an isolated install environment such as
  `pipx`. Python 3.15 is not supported. Python 3.11 or newer is recommended
  when using the sandbox.
- **Git and network access to GitHub** so the CLI can fetch its Ansible
  collections on first use. The CLI installs them in its own cache;
  installing `plaibook` also installs its `ansible-core` dependency, so
  no separate Ansible install is needed for the CLI.
- **A provider API key** and network access from this machine to that
  provider's API. OpenAI is the primary provider in the examples below;
  Anthropic and Gemini are supported alternatives.
- **The selected provider's Python SDK.** The CLI checks and installs
  the pinned SDK packages for the selected provider into its own Python
  environment when needed. The checked-in pins are:

  | Provider | CLI family | API key environment variable | Pinned Python SDK |
  |---|---|---|---|
  | OpenAI | `openai` | `OPENAI_API_KEY` | [`openai==3.23.0`](../plaibook/hashed/openai.in) |
  | Anthropic | `claude` | `ANTHROPIC_API_KEY` | [`anthropic[vertex]==1.11.0` and `claude-agent-sdk==0.2.163`](../plaibook/hashed/claude.in) |
  | Gemini | `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | [`google-genai==2.27.0`](../plaibook/hashed/gemini.in) |

- **OpenShell CLI and a reachable gateway named `openshell`** for the
  default sandboxed PR/branch review. Install OpenShell using the
  [OpenShell installation guide](https://docs.nvidia.com/openshell/latest/about/installation),
  then check the connection with `openshell status`. The playbook
  installs its Python SDK
  ([`openshell>=0.1.3,<0.2`](../plaibook/hashed/openshell.in)) when
  needed. You can skip the OpenShell CLI and gateway if you explicitly
  run with `--no-sandbox`.

Do not put API keys in the repository or in `vars.yml`. Keep them in your
shell environment or a secret manager.

## Install and run your first review

Install the CLI in an isolated environment:

```bash
pipx install plaibook
```

Upgrade that install later with `plai update`. Version output and the
upgrade rules are in [Upgrade an installed CLI](#upgrade-an-installed-cli).

Set the OpenAI key, then replace `OWNER`, `REPO`, and `123` with the
GitHub repository and pull request number:

```bash
export OPENAI_API_KEY='<your OpenAI API key>'
plai review --provider openai https://github.com/OWNER/REPO/pull/123
```

`--provider openai` selects OpenAI and saves the selection to
`~/.config/ansible-plaibook/vars.yml`; later reviews can omit the flag.
The CLI installs the pinned provider SDK and Ansible collections when
they are missing. GitHub PR and branch reviews use the OpenShell sandbox
by default. Keep it enabled for public or otherwise untrusted PRs; see
[`Sandboxes and LLM calls`](sandbox-and-agent-safety.md) for how the
isolation works.

### Select Anthropic or Gemini instead

The CLI installs the corresponding pinned SDK when you select another
provider. Set that provider's key and choose its family:

```bash
export ANTHROPIC_API_KEY='<your Anthropic API key>'
plai review --provider claude https://github.com/OWNER/REPO/pull/123
```

```bash
export GEMINI_API_KEY='<your Gemini API key>'
plai review --provider gemini https://github.com/OWNER/REPO/pull/123
```

Gemini also accepts `GOOGLE_API_KEY`. Each `--provider` choice is saved
for later runs.

### Run without the sandbox

For a PR whose source you trust, add `--no-sandbox`:

```bash
plai review --no-sandbox https://github.com/OWNER/REPO/pull/123
```

If selecting a provider at the same time, include both options, for
example `plai review --provider openai --no-sandbox <PR URL>`. Without
the sandbox, OpenShell is not required; the review runs on the local
Python environment with the same provider key and SDK requirements.

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
to `grok-4.7` at effort `high`. `--provider cursor` saves that
without a prompt. A Grok review uses the OpenAI-compatible client, not
Luna:

```bash
plai review org/repo/123 --provider openai -e review_openai_model=grok-4.7
```

That sends the lenses to `https://api.x.ai/v1` when `OPENAI_BASE_URL` is
unset. Set `XAI_API_KEY` or `GROK_API_KEY`. A non-interactive run with
only that key and no saved provider selects `openai` and `grok-4.7`.
Re-reviewing the same SHA is a $0 cache hit unless you pass `-f` /
`--force`.

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

`--sha BASE..TIP` reviews that contiguous range as one diff (`git diff BASE TIP`). `BASE` must be an ancestor of `TIP`. The same commit on both sides prints `NOTHING_TO_REVIEW` and the range, and exits 0. It does not print `status: ok`. Three-dot `BASE...TIP` is rejected. A merge-base diff of a whole branch is `review_type: branch`. Pull-request and branch reviews still exit 0 when the verdict is `NEEDS_CHANGES`. Commit review is the mode that fails the process on a real Critical or Major finding.

## CLI output

Default stdout is a readable review, not ansible TASK spam and not
`last_run.json`. It prints the target, verdict, 0–100 scores,
Critical/Major findings with `file:line` + title + short why, minor/nit
as counts, then a footer (cost, run-scoped `last_run.<run_id>.json`,
path to `findings.md`). A `SKIPPED` verdict (CI failing on the PR head)
prints the reason and failing check names here, not only under `-v`.
`--ignore-ci` (`-e review_require_ci_passing=false`) reviews anyway.
`--triage-ci` (`-e review_triage_ci_failures=true`) explains the failures
and does not dispatch lenses. A score line
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

## Notes

### Upgrade an installed CLI

`plai --version` prints `plaibook 0.1.28` for a PyPI install of this
release. A git install of the same tree prints the revision and a short
commit, for example `plaibook 0.1.28 (dev, main@664b8e1d39ac)`.

`plai update` upgrades the install that is running. A pipx install
uses `pipx install --force --pip-args=--no-cache-dir plaibook==VERSION`.
A `uv tool` install uses `uv tool install --force --no-cache`. A
virtualenv uses that interpreter's pip. An editable checkout is left
alone. The command compares the version inside that install, not a
different copy that happens to be on the machine. It reports the PyPI
release as already installed only when that install's version matches
and its recorded source is PyPI. A previous `plai update --branch`
does not stick, including when that ref's version already matches the
PyPI release. `--branch` checks the recorded spec after install.
`plai update --check` compares versions and does not install. A
non-interactive run must pass `-y` or `--yes`. Without a terminal, the
command cancels.

### Install from a checkout or another isolated environment

If you want to install this checkout instead of a published package,
create a virtual environment and install the project from the repository
root. `python3 -m venv` includes `pip`, which the CLI uses to install a
missing provider SDK into that environment:

```bash
git clone https://github.com/aknochow/ansible-plaibook.git
cd ansible-plaibook
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

`uv tool install plaibook` is another isolated CLI install when the
package version you need is available. Avoid installing into Homebrew or
Debian system Python, which may enforce PEP 668. On macOS, keep virtual
environments out of `/tmp`: XProtect can block scripts that appear there
and then run.

### Install SDKs for a `uv` development environment

When launching `ansible-playbook` directly from a `uv` development
environment, `uv sync --extra dev` does not install provider SDKs. After
syncing, install the hashed requirements for the provider you will use.
First sync the project:

```bash
uv sync --extra dev
```

Then run exactly one provider install command:

**OpenAI**

```bash
uv pip install --python .venv/bin/python --require-hashes \
  -r plaibook/hashed/openai-requirements.txt
```

**Anthropic**

```bash
uv pip install --python .venv/bin/python --require-hashes \
  -r plaibook/hashed/claude-requirements.txt
```

**Gemini**

```bash
uv pip install --python .venv/bin/python --require-hashes \
  -r plaibook/hashed/gemini-requirements.txt
```

Then run the playbook from the checkout root. The example selects OpenAI;
for Anthropic or Gemini, change `agent_family` to `claude` or `gemini`
and export the matching API key from the prerequisites table above.

```bash
export OPENAI_API_KEY='<your OpenAI API key>'
uv run --no-sync ansible-playbook review.yml \
  -e agent_family=openai \
  -e review_targets_raw='https://github.com/OWNER/REPO/pull/123'
```

The playbook also reads `agent_family` from
`~/.config/ansible-plaibook/vars.yml`, so omit `-e agent_family=openai` if
that file already selects OpenAI. Extra-vars override the saved setting.
For a trusted PR, `-e use_sandbox=false` disables the sandbox on this
direct-playbook path. Raw `ansible-playbook` needs Python 3.11 or newer
for sandboxed PR reviews; unlike the CLI, it does not re-exec a Python
3.10 controller into the sandbox runtime. AAP and execution environments
continue to run `ansible-playbook review.yml` directly with their
dependencies included in the environment.

### Python versions, sandbox, and first-run caches

Python 3.10 through 3.14 are supported. Python 3.15 is not supported.
Python 3.10 uses the ansible-core 2.16 series; Python 3.11 and newer
permit ansible-core 2.16 through 2.19. The OpenShell sandbox runtime uses
Python 3.11 or newer. When `plai review` starts on Python 3.10, it creates
`~/.cache/ansible-plaibook/sandbox-runtime` using the first Python 3.11+
interpreter it finds, installs this plaibook build and the needed
OpenShell and provider SDKs there, then continues with that interpreter.
`--no-sandbox` stays on the original interpreter.

On first use, the CLI fetches collections from GitHub into
`~/.cache/ansible-plaibook/collections`; it does not install into
`~/.ansible` or use galaxy.ansible.com. CLI scratch files are kept in
`~/.cache/ansible-plaibook/tmp`.

With the default sandbox policy, model API calls stay on the controller;
the sandbox does not need access to OpenAI, Anthropic, or Gemini. Guest
network rules matter if you replace `sandbox_policy` (which replaces the
gateway's default network policy) or run checklist commands that need
egress. See
[`Sandboxes and LLM calls`](sandbox-and-agent-safety.md#openshell-network-policy-vs-provider-apis)
for the network policy details.
