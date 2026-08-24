---
name: ansible-plaibook-review
description: "Invoke this repo's AI code-review playbook (review.yml) and read its structured output correctly. Use whenever asked to review a PR/MR, review a local commit, or interpret a prior run's verdict/findings in this repo."
---

# ansible-plaibook review pipeline

This repo (`ansible-plaibook`) is an Ansible-native AI code-review pipeline.
One top-level playbook, `ansible-playbook review.yml` from the repo
root, dispatching on `review_type` (`pr` | `branch` | `commit`).
**Read this whole file before invoking anything** — the output-reading
section is not optional context, it's how you avoid grepping fragile
stdout.

## `review.yml` — one playbook, `review_type` selects the mode

### `review_type: pr` (default) — review a GitHub PR or GitLab MR

```bash
ansible-playbook review.yml -e review_targets_raw="org/repo#123"
ansible-playbook review.yml -e review_targets_raw="https://github.com/org/repo/pull/12"
ansible-playbook review.yml -e review_targets_raw="org/repo!34" -e use_sandbox=false
```

- Plain `-e key=value` extra-vars — no quoting or JSON brackets needed
  for a single target.
- `review_targets_raw` accepts a GitHub PR URL, a GitLab MR URL, or a
  bare `org/repo#N` (GitHub) / `org/repo!N` (GitLab) identifier. For a
  self-hosted GitLab instance (e.g. `gitlab.example.com`), prefix
  the bare GitLab form with the host: `host:org/repo!N`, e.g.
  `gitlab.example.com:aknochow/ansible-plaibook!29` — the same terse
  `host:path` syntax `git`/`scp` already use. Without a host prefix,
  the bare GitLab form still resolves to `gitlab.com`. The full URL
  form works too and needs no prefix. **Both bare GitLab forms (with
  or without a host prefix) only support a single-level `org/repo`,
  not a GitLab subgroup** (e.g. `group/subgroup/project!N`) — use the
  full URL form for a subgrouped project, which does support it.
- For **multiple targets** in one run: either pass `review_targets_raw`
  as a newline-separated string, or use the JSON-list form directly:
  `ansible-playbook review.yml -e '{"review_targets": ["org/repo#1", "org/repo#2"]}'`
- Runs in an OpenShell sandbox by default (`use_sandbox: true` —
  `library/run_checklist.py` executes commands parsed out of a
  source-branch-controlled `CHECKLIST.md`, i.e. untrusted input). Set
  `use_sandbox=false` to skip sandboxing.
- `post_results` defaults to `false` — reviewing is safe to automate;
  posting the review back to the real PR/MR is a write to shared state
  and needs explicit opt-in (`-e post_results=true`).
- Use `-e review_extra_notes="..."` to give the reviewer free-text,
  run-only operator context — see "review_extra_notes" below.

### `review_type: commit` — fast local single-commit check

```bash
ansible-playbook review.yml -e review_type=commit
ansible-playbook review.yml -e review_type=commit -e commit_sha=abc1234 -e repo_path=/path/to/repo
```

- Both `commit_sha`/`repo_path` are optional: `commit_sha` defaults to
  `HEAD`, `repo_path` defaults to the current working directory.
- No sandbox by default, no exploration pass, no PR/MR API calls —
  meant for a tight feedback loop right after a commit (e.g. a Claude
  Code `PostToolUse` hook on `git commit`), **not** a replacement for a
  full `review_type: pr`/`branch` review.
- **Different exit-code behavior than `pr`/`branch`**: those never fail
  the Ansible run based on verdict (a `NEEDS_CHANGES` review is still a
  "successful" playbook run). `review_type: commit` instead **fails
  (non-zero exit)** when the verdict is `NEEDS_CHANGES` with a real
  Critical/Major finding, so a hook can gate directly on the exit code:
  `if ! ansible-playbook review.yml -e review_type=commit; then ...; fi`.
  Set `-e fail_on_regressions=false` to always exit 0 regardless of
  verdict.

### `review_type: branch` — whole-branch/codebase audit

No caller wires this up yet (no CLI convenience beyond
`-e review_type=branch -e branch_review_target=...` and setting the
right target-parsing vars) — it exists in `roles/review/` but is
otherwise dormant. Not part of the CLI-supported surface today.

## Which mode to use

| Situation | Command |
|---|---|
| Someone (or something) opened a GitHub PR or GitLab MR and it needs a review | `review.yml` (default `review_type: pr`) |
| You just made a local commit and want a fast sanity check before pushing/opening a PR | `review.yml -e review_type=commit` |

## Reading the output — do this, not stdout-grepping

Every run prints two clean debug lines at the end:
- `RESULT_SUMMARY_RUN_SCOPED: ~/.cache/ansible-plaibook/last_run.<run_id>.json`
  — **read this one. Always.** It's this invocation's own result, named
  after the run's own `run_id`, immune to being overwritten by any other
  concurrent run.
- `RESULT_SUMMARY: ~/.cache/ansible-plaibook/last_run.json` — a fixed,
  predictable path with the same content, **but it's a shared path any
  concurrent invocation of review.yml (any review_type) overwrites**
  (this repo or a different one, this terminal or a different Claude Code
  session). Two agents live-dogfooding this repo in parallel have already
  hit this for real: one read back a completely different repo's review
  with no error or mismatch warning. **Never read the fixed path
  programmatically** — it's a quick-glance convenience for a human `cat`
  who knows nothing else on the machine is running a review right now,
  nothing more. If you don't have the run-scoped path (e.g. checking a
  past result in a later conversation) and must read the fixed path,
  verify it first: `python3 scripts/check_last_run_target.py "org/repo#123"`
  loudly refuses (exit 1, clear stderr message) if the file currently
  describes a different target than the one you asked about, instead of
  silently handing back whatever's there.

Extract the run-scoped path from your own invocation's stdout — don't
guess it, don't reuse one from a previous run. Shape (identical for both
paths):

```json
{
  "run_id": "a1B2c3D4e5F6g7H8",
  "targets": [
    {
      "target": "org/repo#123",
      "report": "<full rendered findings.md text>",
      "verdict": "READY_FOR_HUMAN_REVIEW | NEEDS_CHANGES",
      "score": 93.3,
      "summary_path": "/path/to/summary.json",
      "findings_path": "/path/to/findings.md"
    }
  ],
  "cost_usd": 0.1234,
  "total_input_tokens": 12345,
  "total_output_tokens": 6789,
  "agents_dispatched": 3
}
```

These are the only things worth grepping stdout for, if you insist on
reading stdout at all. You shouldn't need to: verdict, score, and the
full report text are already in the JSON, no timestamped debug-log
parsing required. `review_type: commit` produces a `targets` list with
exactly one entry, shaped identically to the others (no need to
special-case which review_type produced this file).

If `last_run.json`'s inline `score`/`verdict`/`report` aren't enough
detail, drill into that target's `summary_path` — a structured
`summary.json`:

```json
{
  "target": "org/repo#123",
  "commit": "abc1234",
  "branch": "github-org-repo-123",
  "date": "2026-07-30",
  "verdict": "NEEDS_CHANGES",
  "score_overall": 66.7,
  "scores": {"functionality": 80.0, "security": 50.0, "quality": 70.0},
  "findings_count": {"critical": 0, "major": 1, "minor": 2, "nit": 0},
  "findings": [ /* full per-finding objects: file, line, severity, lens, evidence, fix, confidence, ... */ ],
  "findings_path": "/path/to/findings.md"
}
```

Only fall back to `findings_path` (the full human-readable `findings.md`
prose report) if you need the narrative writeup for a deep dive —
treat it as a **last resort**, not the first thing you read. Never grep
raw Ansible stdout for findings; the structured JSON is deterministic
and complete, stdout is timestamped debug soup.

## Verdict rule (for interpreting scores/findings yourself)

Any surviving Critical or Major finding forces `NEEDS_CHANGES`, full
stop — no confidence carve-out, no exceptions
(`roles/review/tasks/merge.yml`). Per-lens and overall scores are
on a 0-100 scale, computed deterministically as `max(10, 100 - sum(finding
severity points))` (`action_plugins/
compute_review_scores.py`) from the surviving findings list itself —
never trust a self-reported score that seems inconsistent with the
findings array (a lens can self-verify a finding away in its prose
without recomputing its own score line to match; the pipeline
recomputes scores from `deduped_findings` specifically to avoid this
desync). Displayed as a genuine percent (e.g. "97.3%"), not a "/100"
fraction.

## Known gotchas

- **The self-refuted-finding filter is imperfect — treat a surviving
  LOW-confidence finding with extra skepticism, not blind trust.** The
  reviewer prompt tells the model to self-verify each finding and
  silently drop it from the findings array if it talks itself out of
  it during its own validation chain. In practice the model does not
  reliably do this: `roles/review/tasks/merge.yml` documents
  **seven** distinct real wordings caught live where a finding was
  semantically retracted (in the `evidence`, `fix`, or `description`
  field) but still shipped in the findings array — e.g. "N/A —
  self-refuted, dropping materiality", "Logic is correct upon
  re-examination — dropping.", or a retraction that lives entirely in
  `description` while the `fix` field reads like a normal, actionable
  suggestion on its own. The pipeline applies a widening regex
  (`rejectattr` on `evidence`/`fix`/`description`) as a deterministic
  backstop, asserts nothing matching survives, and even so this is an
  imperfect, reactive filter — every new escape-hatch wording has
  defeated the previous, narrower regex. **A confidence gate on
  findings was deliberately reverted** (merge.yml's own comment): don't
  assume a LOW-confidence finding is safe to ignore — that's exactly
  the "trust a self-reported model field" failure mode this codebase
  has already been burned by twice. Read the actual `evidence`/`fix`
  text yourself before treating any finding, especially a LOW-confidence
  one, as settled.
- **Model non-determinism across runs.** The only genuinely LLM-driven
  part of this pipeline is the pair of independent lens calls
  (`aknochow.claude.message`, Security + Functionality/Quality)
  dispatched per review, plus the exploration pass and bug-fix
  agentic loop where applicable. Re-running the same playbook against
  the exact same diff/commit is not guaranteed to reproduce identical
  findings, scores, or verdict — the merge/dedup/scoring math in
  `merge.yml` is deterministic given a set of findings, but the
  findings themselves come from live model calls. Don't treat a single
  run's verdict as a ground-truth oracle; a borderline `NEEDS_CHANGES`
  right at the Critical/Major boundary is worth a second look, not
  automatic hard-gating in a fully unattended pipeline.
- **`review_extra_notes` is free-text, run-only, trusted operator
  context — not a findings override.** Set it to tell a re-review
  something it keeps getting wrong, e.g.
  `-e review_extra_notes="the always-visible dropdown is an intentional design decision, not a bug"`.
  It's threaded into every LENS's system prompt as TRUSTED input
  (unlike the diff/PR description, which are explicitly untrusted), and
  applies uniformly to every target in a multi-target `review.yml` run.
  It is **not persisted anywhere** — it's scoped to that one run only.
  It is guidance, not an override: per `roles/review/defaults/main.yml`'s
  own comment, a note claiming something is fine doesn't exempt it from
  the validation chain if it plainly isn't. Works the same way with
  `review_type: commit` too — `roles/review/defaults/main.yml` has no
  default for this var at all; `review.yml` sets its own empty-string
  default once, for every review_type, since it's meant to be a
  per-run, per-caller override.
  - **Never reaches `verify.yml`, on purpose.** The independent
    Critical/Major re-check stage is deliberately kept blind to operator
    context — confirmed empirically (not just by design intent) that
    giving it the note makes an otherwise-reliable check unreliable: the
    identical, technically-true fixture landed on three different
    verdicts (`refuted`/`verified`/`inconclusive`) once the note reached
    it, versus consistently `verified` without. Don't try to "fix" a
    verify-stage finding by making it see notes — that's a known,
    deliberate non-goal, not an oversight. If a finding is real but
    already decided (deferred/accepted), that's a separate concern from
    whether it's technically true, and doesn't yet have a mechanism —
    see handoff.ansible-plaibook-review-extra-notes-suppression-mechanism.yaml.
  - **Write the note around the accepted OUTCOME, not implementation
    details.** A note naming specific functions/lines only reliably
    suppresses findings about THOSE functions' own internal logic — it
    doesn't automatically cover every downstream symptom of the same
    root cause (confirmed empirically: a note naming two functions by
    name suppressed findings about them 2 times out of 3, but the third
    run found a technically distinct, legitimate concern about a
    downstream consumer of their output that the note never mentioned).
    Prefer "the footer may show inconsistent timestamp formatting
    depending on source data — accepted, don't flag any variant of
    this" over "get_generated()/_coerce_to_datetime()'s behavior is
    fine" — describe the user-visible behavior you've accepted, not just
    the code that produces it.
  - **Passing it via `-e key=value` truncates at the first colon, silently.**
    `-e review_extra_notes="Note: this is intentional"` actually sets
    the note to `"Note"` — Ansible's legacy `-e key=value` CLI parsing
    does this with no error or warning. Any note phrased like a label
    ("Note:", "Context:", a URL, "See PR #3: ...") is at risk. Use the
    JSON-list form instead whenever the note might contain a colon:
    `-e '{"review_extra_notes": "Note: this is intentional"}'`.
  - **Unsure whether your note actually reached the agent, or where it
    landed in the prompt?** Set `-e review_debug_dump_prompts=true` —
    every rendered lens/explore/verify system prompt gets written to
    `~/.cache/ansible-plaibook/debug_prompts/<review_branch>/*.txt` (path
    printed via `DEBUG_PROMPT_DUMP:` debug lines), so you can read
    exactly what the agent saw instead of inferring from Jinja
    templates. Off by default; overwritten every run for the same
    target, not a history.
  - **On a same-commit re-review, your note may never reach a model.**
    The same-commit fast path (`review_same_commit_fast_path_enabled`,
    default `true`) replays the prior result without re-dispatching
    lenses when re-reviewing an already-reviewed commit —
    `review_extra_notes` has no effect in that case. Pass
    `-e review_same_commit_fast_path_enabled=false` to force a fresh
    dispatch.
- **Sandbox teardown vs. debugging**: `review.yml` defaults
  `cleanup_sandbox_onfail=true` — a failed
  run tears its sandbox down by default. Set
  `-e cleanup_sandbox_onfail=false` if you need to SSH into a failed
  run's sandbox for debugging instead of losing it immediately.
