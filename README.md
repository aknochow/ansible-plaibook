# ansible-plaibook

AI-powered code review, running as a deterministic, unattended Ansible
playbook, not a chat-based agent session. It ports the methodology of
an interactive `code-review` skill into pure Ansible: dispatch
independent LLM lenses over a diff, merge and score their findings with
plain deterministic math (never trusted self-report), and render a
report, the same review a human would give, produced the same way
every time, invocable from a terminal, a git hook, or an AAP job
template. Expect more playbooks here over time as other harness skills
get the same treatment.

## Why Ansible?

The question everyone asks first. A few concrete reasons this isn't
just "a Python script that happens to be YAML":

- **It ships as an AAP Job Template for free.** This isn't
  hypothetical. `review.yml` already runs as a real job template
  against real MRs (see the "Running it in AAP" section below).
  Whatever RBAC, credential management, scheduling, and audit logging
  your team already runs its infra automation through, your AI code
  review runs through too. No bespoke service to stand up, no new
  auth model to build.
- **Every step is a named, inspectable, replayable task**, not opaque
  agent-framework internals. `ansible.posix.profile_tasks` timing is
  already wired in; every review's cost/token usage is tracked per
  agent call. When something looks wrong, you read the task list, not
  a stack trace from a framework you don't own.
- **Scores are never self-reported.** `merge.yml`/`verify.yml` recompute
  every score deterministically (0-100 scale) from the surviving
  findings list via a shared Python action plugin. A lens can talk
  itself out of a finding in prose, but that doesn't change the score
  unless the finding is actually dropped from the list. That's a
  discipline Ansible's plain, auditable task model makes natural; a more
  "clever" agent framework makes it easy to accidentally trust a
  model's self-assessment.
- **Sandboxing is a first-class primitive, not bolted on.** Every
  script-touching task takes its execution target from a
  `review_delegate_host` var. Sandbox lifecycle lives once in the
  top-level playbook, and the `review` role has zero knowledge OpenShell
  even exists. That's just `delegate_to`, not custom isolation plumbing.
- **Cost.** No persistent agent service, no idle infra. This runs
  on-demand and you pay for exactly the LLM calls a review actually
  makes. A single review typically costs a few cents to ~$0.20
  depending on findings/exploration depth (see a real example below).

## More reasons for Ansible

Beyond the review pipeline's own design, building this as a plain
Ansible playbook and role means the entire mature `ansible-*` tooling
ecosystem comes along for free. None of it is built or maintained by
this project, and all of it is already production-hardened at
enterprise scale elsewhere.

- **`ansible-lint`** enforces real code quality automatically, not by
  convention. This repo runs at the `production` profile, Ansible's
  strictest ("meets requirements for inclusion as validated or
  certified content"), wired into a pre-commit hook via
  `.ansible-lint`.
- **`ansible-builder`** turns `execution-environment.yml` into a
  runnable, portable container image with the right Python and
  collection dependencies baked in. No hand-maintained Dockerfile
  (see "Running it in AAP" below).
- **`ansible-galaxy`** handles collection dependency resolution and
  distribution. The provider collections this pipeline depends on
  install the same way any other Galaxy collection does, via
  `collections-requirements.yml`. No custom package manager to build.
- **`ansible-navigator`** runs and inspects playbooks against the exact
  execution environment `ansible-builder` produces, without a separate
  install-and-debug step. Compatible out of the box, since
  `execution-environment.yml` already exists.
- **AWX and Ansible Automation Platform** provide the actual production
  job runner: RBAC, credential injection, scheduling, and audit
  logging, all already covered above. `ansible-runner` is what AAP
  itself uses under the hood to execute each job.
- **`ansible-doc`** documents every module, plugin, and role variable
  this project ships, the same discoverability any other collection
  gets, with no separate docs generator to maintain.

## How `review.yml` works

One playbook, `review_type` selects the mode (`pr` | `branch` |
`commit`), one or more targets, five stages:

1. **Briefing** (`briefing.yml`), deterministic, zero-LLM: diffing,
   domain detection, prior-review lookup, and checklist execution, all
   real Ansible tasks and action plugins (no vendored harness scripts
   left in this stage as of the action-plugin migration).
2. **Lenses** (`lenses.yml`), two independent `aknochow.claude.message`
   calls dispatched in parallel: a Security lens and a
   Functionality+Quality lens. This is the only genuinely LLM-driven
   step in the base pipeline.
3. **Merge** (`merge.yml`), dedup findings across lenses, apply a
   self-refutation filter (models sometimes talk themselves out of a
   finding in prose without dropping it from the list, this backstops
   that), and compute scores/verdict deterministically. Any surviving
   Critical or Major finding forces `NEEDS_CHANGES`, full stop.
4. **Explore** (`explore.yml`), a bounded, tool-calling pass
   (`read_file`/`search`, read-only) that looks beyond the diff itself
   for related context before finalizing findings.
5. **Persist** (`persist.yml`), renders the human-readable report and
   writes a structured `summary.json`, plus a stable
   `~/.cache/ansible-plaibook/last_run.json` every run overwrites (so tooling
   always knows where to look, no timestamp-guessing).

```bash
ansible-playbook review.yml -e review_targets_raw="org/repo#123"
ansible-playbook review.yml -e review_targets_raw="https://gitlab.example.com/org/repo/-/merge_requests/45"
```

Runs in an OpenShell sandbox by default (`use_sandbox: true`) since
briefing execution runs commands parsed out of source-branch-controlled
input. `post_results` defaults to `false`, reviewing is safe to
automate; posting back to a real PR/MR is a write to shared state and
needs explicit opt-in.

## `review_type: commit`: fast local checks

A lightweight mode for a tight feedback loop right after a local
commit: no sandbox by default, no exploration pass, no PR/MR API
calls, just the Security + Functionality/Quality lenses against your
working tree.

```bash
ansible-playbook review.yml -e review_type=commit
ansible-playbook review.yml -e review_type=commit -e commit_sha=abc1234 -e repo_path=/path/to/repo
```

Unlike `review_type: pr`/`branch` (which never fail the Ansible run
based on verdict, a `NEEDS_CHANGES` review is still a "successful"
run), `review_type: commit` **fails its exit code** when the verdict
is `NEEDS_CHANGES` with a real Critical/Major finding, built for
hooking into something like a Claude Code `PostToolUse` hook on `git
commit`, so a coding agent gets live regression feedback on every
commit it makes, not just at PR time.

This used to be a separate `commit_review.yml` playbook, duplicating a
fair amount of `review.yml`'s tail logic (cache-dir handling,
`last_run.json` writing, stats) and target-resolution logic. Both are
now unified: one playbook, `review_type` drives the mode-specific
defaults (sandboxing, exploration depth, fail-on-verdict behavior) via
plain variable defaults, not separate control flow.

## Architecture

One role, `review`, powers `review.yml`. It used to be two (`review` for
target resolution handing off via `include_role` to a separate
`code_review` engine), merged into one role once that split had proven
itself as a valid intermediate step, per the same "don't leave a
deliberate-but-temporary shape in place longer than it needs to be"
discipline this repo applies elsewhere. `roles/review/tasks/main.yml`
dispatches on `review_type` (`pr` | `branch` | `commit`) to
`resolve_target_{{ review_type }}.yml`, which resolves the target into
the output-variable contract the shared review engine expects
(`review_repo_path`, `review_org_repo`, `review_branch`,
`review_context`, `review_short_sha`, etc.), then calls the engine
itself (`run_review_engine.yml`: briefing, lens dispatch, merge/dedup,
explore, verify, persist). Target-resolution files and engine files
are distinguished by naming convention now that they're not separated
by a role boundary: `resolve_target_*.yml` contains ONLY logic
specific to that target type; `briefing.yml`/`lenses.yml`/`merge.yml`/
`explore.yml`/`verify.yml`/`persist.yml` are the shared engine, agnostic
to which `resolve_target_*.yml` produced its inputs.

- `review_type: pr` detects GitHub vs. GitLab from the target
  identifier, fetches PR/MR context, and resolves the head ref; it
  optionally posts results back via `post_review_comment.py` (gated behind
  `post_results: false` by default), this is the one target type with
  its own post-engine step, which is why each `resolve_target_*.yml`
  calls the engine itself rather than the top-level dispatcher calling
  it uniformly for all three.
- `review_type: commit` resolves/validates a local commit directly via
  git, no PR/MR API involved.
- `review_type: branch` resolves a whole local-path/GitHub/GitLab branch
  target; it has no CLI-wired caller yet (built ahead of a planned
  whole-branch/codebase review capability).

Domain-specific review steering (tech-stack-specific guidance blocks,
ported from harness's `domain_steering.md`) live as individual Jinja
templates under `roles/review/templates/domain_steering/`, one file
per domain, independently editable/reviewable.

Phase 2 (sandboxed) runs stand up an OpenShell sandbox for isolated
script execution and register it as a real Ansible inventory host over
SSH, delegating individual tasks to it as needed, while every
Claude/Gemini call still runs on the controller, never inside the
sandbox. See [`docs/sandbox-and-agent-safety.md`](docs/sandbox-and-agent-safety.md)
for the full pattern (and the community pattern we deliberately don't
use instead).

### Bug-fix pipeline: parked, not on `main` right now

A second tenant (`bug_context`/`bug_plan`/`bug_fix` roles +
`bug_pipeline.yml`) is deliberately parked on the `wip/bug-fix-pipeline`
branch while the review pipeline's foundation (this repo's actual star)
gets an independent verification stage and an evals suite first. It
comes back once that foundation is solid, review + fix together is
the next logical milestone.

### Verification auditor: in flight

An independent, adversarial re-check of Critical/Major findings
(read-only tool-calling agent, given only one finding at a time,
instructed to try to *refute* it) is under active development on
`feat/verification-auditor`, catches the class of false positive that
survives self-verification (a model confidently misremembering an SDK
signature or stdlib default) by actually reading the source it depends
on. Not yet merged.

## Running it in AAP

`execution-environment.yml` builds a purpose-built EE with both sibling
collections and their Python dependencies baked in, plus `gh`/`glab`
for PR/MR API access. This design has already run as a real AAP Job
Template against real MRs. The maintainer publishes a prebuilt image at
`quay.io/aknochow/plaibook-ee` (a CI pipeline to build and publish it
automatically is coming in a follow-up PR); to build and host your own
instead, tag and push to a registry you control:

```bash
ansible-builder build -t <your-registry>/plaibook-ee:latest -f execution-environment.yml
podman push <your-registry>/plaibook-ee:latest
```

Credentials (GitHub/GitLab tokens, OpenShell mTLS) are injected via
AAP credentials at job-run time, never baked into the image.

## Verified so far

- ✅ Full pipeline against a real local diff, unsandboxed, and
  identically delegated into a real OpenShell sandbox
- ✅ Looping over multiple targets in one run
- ✅ Real PR/MR review via `review` (`review_type: pr`) against real
  GitHub and GitLab targets
- ✅ Running as a real AAP Job Template against a real MR
- ⏳ `post_results: true` (posting back to a real PR/MR)
- 🚧 Verification auditor (`feat/verification-auditor`, not yet merged)
- 🚧 Bug-fix pipeline, parked on `wip/bug-fix-pipeline`, comes back
  once the review pipeline's foundation is fully solid

## Known gotchas (hit during development, worth knowing before debugging blind)

- **Vertex org policy**: `constraints/vertexai.allowedPartnerModelFeatures`
  can block `output_config` (native structured output) on some GCP
  projects while plain messages/forced-tool-use still work fine on the
  *same* project/model. This pipeline uses `tool_choice`-forced
  structured output specifically to sidestep that. See
  `roles/review/tasks/lenses.yml`.
- **Sandbox network policy is separate from provider attachment.**
  Don't assume a sandbox can reach an internal GitLab or an arbitrary
  API just because a provider is attached; check the gateway's network
  policy.
- **EE collection tarballs are pinned, not live.** `execution-environment.yml`
  bakes in `build/collections/*.tar.gz` snapshots of the sibling
  collections, not their live git state. Rebuild both
  (`ansible-galaxy collection build ~/code/ansible-openshell --output-path build/collections -f`,
  same for `ansible-claude`) and rebuild/push the EE whenever either
  collection changes, or AAP jobs will run against stale module code.
- **`sandbox_gateway` must be `host.openshell.internal`, not
  `127.0.0.1`/`localhost`, and that requires a manual `/etc/hosts`
  entry.** OpenShell's podman driver splits the gateway into two
  listeners: a primary (full API, including sandbox creation) and a
  restricted compute-driver-callback-only listener. On a dual-stack
  host they land on different addresses (primary on `[::1]`, callback
  on `127.0.0.1`), so `127.0.0.1`/`localhost` non-deterministically
  hits the wrong one and sandbox creation fails with
  `PERMISSION_DENIED: compute-driver callback listeners accept
  sandbox callback RPCs only`. `host.openshell.internal` is already
  one of the SANs on OpenShell's own generated gateway TLS cert and
  always resolves to the primary listener, but nothing makes it
  actually resolve unless you add it to `/etc/hosts` yourself:
  `echo "::1 host.openshell.internal" | sudo tee -a /etc/hosts`.
  Also: OpenShell 0.0.106's own documented default registration
  (`openshell gateway add https://[::1]:17670`, exactly what the
  Homebrew formula's install caveat recommends) fails outright with
  `invalid dns name`, a raw IPv6 literal isn't accepted as a TLS SNI
  name by their CLI, with or without `--gateway-insecure`. This is an
  upstream OpenShell bug, not something fixable from here; the
  `/etc/hosts` route above is the least-bad workaround until it's
  fixed upstream.
