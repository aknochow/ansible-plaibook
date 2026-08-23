---
type: Concept
title: Architecture
description: How the review pipeline is built, the consolidated review role, domain-specific steering, and independent verification.
tags: [architecture, design, security]
status: stable
---

# Architecture

## One role, three entry points

`roles/review/` is the single role behind every review type
(`review_type=pr|branch|commit`). Earlier in the project's history these
were three separate roles (`pr_review`, `branch_review`, `code_review`);
they were consolidated into one, dispatching on `review_type` rather than
duplicating the pipeline three times.

## The review pass

Two independent lens agents run on the same diff: **Security** and
**Functionality/Quality**. Each gets its own system prompt
(`security_agent_prompt.j2` / `review_agent_prompt.j2`), its own
severity-classification rules, and, critically, the same explicit
prompt-injection defense framing, since the diff, PR description, and
commit messages they're reading are all untrusted input that may contain
text attempting to manipulate the review itself.

Domain-specific guidance is layered on top automatically:
`detect_diff_domains.py` inspects the diff and selects zero or more
blocks from `roles/review/templates/domain_steering/*.md.j2` (Python
code, Ansible, Kubernetes operators, shell scripts, and others) to inject
into the lens prompts for that run only. New domain-specific checks
belong here, not in the universal lens prompts.

## The verification pass

Every surviving Critical or Major finding is re-checked by an
independent call with no memory of the reasoning that produced the
original finding, the same "structure the judgment, then check it
separately" pattern used throughout the pipeline. This is what lets the
tool say "this looks like an XSS vector... on closer inspection, refuted"
in the same report, rather than shipping every plausible-sounding claim
as fact.

A newer pass, finding-identity continuity, extends the same idea across
review *rounds*: when a finding recurs after a fix, an independent audit
call confirms or refutes whether it's genuinely the same finding before
a `finding_id` carries forward. The audit call is a second, fresh
conversation. It doesn't inherit the claiming turn's own reasoning,
for the same reason the Critical/Major verify pass doesn't inherit the
lens's own reasoning.

## Known, load-bearing gaps

- **No `pyproject.toml`/lockfile yet.** `ansible-core`/`jinja2` versions
  can drift across separate checkouts (e.g. git worktrees), producing
  different test results for identical code. If a test result looks
  inconsistent with another checkout of the same commit, check dependency
  versions before assuming a logic bug.
- **`~/.cache/ansible-plaibook/last_run.json` is a single global path** with no
  per-session isolation. Concurrent invocations on the same machine race
  to overwrite it. See [Getting Started](getting-started.md#reading-the-output).
- **The self-refuted-finding filter is a reactive regex backstop, not a
  guarantee.** The lens prompt asks the model to self-verify and drop a
  finding it's talked itself out of; in practice this doesn't always
  happen, and `merge.yml` catches several known wordings of a
  semantically-retracted-but-still-shipped finding. Treat a surviving
  low-confidence finding with real skepticism, not blind trust.

## Multi-model dispatch

Where a stage supports more than one model/provider (Claude, Gemini,
and, as of the `aknochow.llama` collection, a locally-hosted Qwen
model), dispatch is explicit per-provider branches guarded by a config
variable, not a generic provider-abstraction class hierarchy. This was a
deliberate decision, not an oversight: the actual provider mechanics
differ enough (forced `tool_choice` vs. `response_schema` vs.
`response_format`, for instance) that an abstraction layer would add
indirection without removing real complexity, for only two or three
providers.
