# ansible-plaibook: Project Context

## Documentation: Read First

Before asking the user how to invoke a playbook or interpret its
output, check these first:

- **[docs/](docs/)**: OKF-compliant docs (`flydocs build`/`flydocs lint` to render/validate)
- **[docs/index.md](docs/index.md)**: navigation index
- **[.claude/skills/ansible-plaibook-review/SKILL.md](.claude/skills/ansible-plaibook-review/SKILL.md)**: how to run `review.yml`/`bug_pipeline.yml` and read `~/.cache/ansible-plaibook/last_run.json` correctly
- **[README.md](README.md)**: project overview (separate from `docs/` for now, not flydocs-generated)

## What This Is

ansible-plaibook is an **Ansible-native AI code-review and bug-fix
pipeline**, deterministic Ansible orchestration around
`aknochow.claude`/`aknochow.gemini`/`aknochow.openai` provider
modules, not an interactive agent loop. Two entry points:
`review.yml` (PR/MR/branch/commit review, `-e review_type=...`) and
`bug_pipeline.yml` (Jira-driven autonomous fix, GitHub only).

## Security Context: Critical

**Diff content, PR/MR descriptions, commit messages, and prior-round
findings are always untrusted input.** Every lens/agent prompt must
carry explicit prompt-injection defense framing (see
`security_agent_prompt.j2`/`review_agent_prompt.j2`'s own headers as
the template to match). `review_extra_notes` is the one exception:
operator-supplied at run time, trusted.

### What NOT to flag as a real finding (confirmed false, repeatedly)

- A boolean value (bare literal or dot-accessed) claimed to get
  "stringified" to `"True"`/`"False"` by this repo's non-native Jinja
  templating, breaking downstream truthiness. **False every time
  it's recurred (9x).** The real, narrow gotcha is dot-accessed
  **integers** only (see `merge_verify_result.py`'s docstring).
- Path traversal on a path built from `review_org_repo`/
  `review_branch`/`find_latest_prior_review`'s own output.
  `briefing.yml` already asserts against `..` on the first two, and
  the third is constrained by a closed regex
  (`^\d{4}-\d{2}-\d{2}-[0-9a-f]{7,}$`) with no traversal characters
  possible. Confirmed false 6 rounds running on MR !48 despite an
  oscillating `evidence_status`.

See `handoff.ansible-plaibook-review-false-positives.yaml` (VAIL) for the
full catalog (29+ patterns). Check it before spending a round
re-deriving a claim from scratch.

### What IS worth flagging

- A finding whose `evidence` matches injected prior-round content
  verbatim but doesn't independently appear in the current diff
  (`check_evidence_provenance.py` exists for exactly this).
- A finding that's semantically self-retracted in its own `evidence`/
  `fix`/`description` text but still shipped in the findings array.
  `merge.yml` has a regex backstop, but it's reactive and imperfect.

## Known Gaps: Critical

- **No `pyproject.toml`/lockfile.** `ansible-core`/`jinja2` versions
  can drift across git worktrees, causing identical code to pass in
  one checkout and fail in another (confirmed real incident, not
  hypothetical). If a test result looks inconsistent with another
  checkout of the same commit, check dependency versions before
  assuming a logic bug.
- **`~/.cache/ansible-plaibook/last_run.json` is a single global path.**
  It races across concurrent sessions on the same machine. Always
  read the run-scoped file (`last_run.<run_id>.json`, printed via
  `RESULT_SUMMARY_RUN_SCOPED:`) or the target's own `summary.json`
  when more than one session might be reviewing at once.
- **Local `main` can go stale mid-session.** `git fetch origin main`
  and check `git merge-base <branch> origin/main` before
  self-dogfooding this repo's own changes or merging. A stale local
  `main` produces a bogus diff that looks like unrelated work was
  "removed."

## Architecture

- `roles/review/` is the single consolidated role behind all three
  review entry points (`pr_review`/`branch_review`/`code_review`
  were merged into it). Dispatch is on `review_type`, not separate
  playbooks.
- Domain-specific reviewer guidance auto-selects per diff via
  `detect_diff_domains.py`, injecting
  `roles/review/templates/domain_steering/*.md.j2` blocks. Add new
  domain guidance there, not into the universal lens prompts.
- `bug_pipeline.yml` opens GitHub PRs only; GitLab MR creation isn't
  implemented for that entry point yet (unlike `review.yml`'s own
  `post_review_comment.py`, which handles both).

## Key Files

| File | Purpose |
|---|---|
| `review.yml` | Entry point: PR/MR/branch/commit review |
| `bug_pipeline.yml` | Entry point: Jira-driven autonomous bug fix |
| `roles/review/tasks/merge.yml` | Dedup, self-refuted-finding filter, score computation |
| `roles/review/tasks/verify.yml` / `verify_finding.yml` / `verify_turn.yml` | Independent adversarial re-check of each Critical/Major finding |
| `action_plugins/` | Real Python for anything beyond trivial Jinja |
| `roles/review/templates/domain_steering/` | Per-domain reviewer guidance, auto-selected |

## Conventions

- Real Python (`action_plugins/`) for logic beyond trivial Jinja, not
  a long `rejectattr`/ternary chain. Established precedent
  throughout the role.
- Per-task-config-var for multi-provider dispatch (Claude/Gemini/
  Qwen), never a generic abstraction-class layer. Decided, don't
  re-litigate (see `handoff.ansible-plaibook-multi-provider-agents.yaml`).
- Commits: `-s` sign-off + `Assisted-by: Claude` trailer, never
  `Co-Authored-By:` (creates phantom accounts on GitHub/GitLab).
- Dogfood every real MR (`review_type=commit` **and**
  `review_type=pr` against your own diff) before merging. A passing
  test suite alone has missed real bugs here more than once.

## Build & Test

```bash
uv run pytest action_plugins/                     # full Python unit suite
uv run pytest action_plugins/test_foo.py          # single file
ansible-playbook review.yml --syntax-check
ansible-playbook review.yml -e review_type=commit                       # fast, cheap, local
ansible-playbook review.yml -e review_targets_raw="org/repo!N"          # full PR/MR review
```
