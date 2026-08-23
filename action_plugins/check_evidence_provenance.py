# -*- coding: utf-8 -*-
"""Shared action plugin: flag findings that echo injected prior-round content.

Implements axis 6 of handoff.ansible-plaibook-verify-yml-scope-expansion.yaml
(fix-prior-review-context-over-trust). CONFIRMED, not hypothesis: ansible-plaibook
MR !25's debug-prompt-dump evidence (~/.cache/ansible-plaibook/debug_prompts/
gitlab-aknochow-ansible-plaibook-25/{review,security}_lens.txt) showed a lens
agent's finding with an `evidence` field that was a character-for-character
match of content living inside the injected "## Prior review" section of
its own prompt (review_agent_prompt.j2/security_agent_prompt.j2, wired by
MR !22) -- the model echoed stale/hallucinated evidence from history instead
of re-deriving it from the actual current diff, overriding its own prompt's
explicit "verify, don't just check if code changed" instruction sitting
directly above that section.

This is the deterministic backstop for that failure mode: a finding whose
evidence text matches the injected prior-round content but does NOT
independently appear in the current diff is mechanically flagged --
`evidence_status: refuted`, the same field/value verify.yml itself uses, so
findings.md.j2 renders it via the existing verification_block macro with no
template changes, and merge.yml's score computation excludes it the same
way verify.yml's later recompute already excludes any other refuted
finding. Not another prompt admonition (the design pass's own framing:
"tell it to be more careful isn't a real fix") -- this can't be talked out
of firing by confident model prose, because it isn't asking the model
anything at all.

Deliberately does NOT touch verify.yml or give it prior-round awareness --
that's axis 3's separate, still-undecided mechanism #3
(expand-verify-yml-prior-round-contradiction-check), explicitly blocked on
ansible-plaibook-stable-finding-id for reliable same-location matching across
rounds. This plugin only needs same-ROUND provenance (this finding's
evidence vs. this round's own injected prior content and this round's own
diff), which needs no cross-round identity at all.

Runs in merge.yml, over the raw combined lens findings, before dedup --
lens findings are the only ones ever exposed to prior_review_md
(explore_agent_prompt.j2 receives existing_findings + full_diff, never
prior_review_md; verify_agent_prompt.j2 receives neither the diff nor
prior-round context at all). A finding this plugin never sees (added later
by explore.yml) was structurally never at risk of this failure mode.

Whitespace-collapsing helper duplicated from dedupe_findings.py's
_collapse_whitespace rather than imported -- no action plugin in this
codebase imports another (confirmed via repo-wide grep before writing
this), matching the same "small amount of duplication is lower regression
risk than a new cross-plugin dependency" reasoning explore.yml's own header
comment already states for its own tool-schema duplication.

Case-SENSITIVE substring matching, unlike dedupe_findings.py's deliberately
case-insensitive dedup keys -- that case-insensitivity replicates a
specific legacy Jinja groupby behavior this port had to preserve exactly.
Nothing here inherits a legacy behavior to replicate; case-sensitive is the
more precise choice for detecting an exact echoed citation, and avoids
spurious matches (e.g. "Return" vs "return") a looser comparison would risk.

_MIN_EVIDENCE_LENGTH_FOR_MATCH guards against trivial short strings (a bare
"return None" or "pass") coincidentally appearing in both the diff and
prior-round prose -- long enough that a real match is a genuine citation
overlap, not a common short token.

Strips a leading '#' comment marker (plus its own indentation) from every
line before comparing, verified necessary against a real MR !25 finding
this module's own test suite reproduces exactly: the echoed evidence
dropped the multi-line comment's own '#' continuation markers when
re-presented as flowing prose ("...an SSH connection\n    # problem
here..." in the original quoted comment vs. "...an SSH connection\nproblem
here..." in the echo) -- without this normalization, an otherwise
character-for-character match fails purely because comment-leader noise
sits in the middle of the shared text. This codebase's evidence is
overwhelmingly '#'-comment languages (YAML, Python, shell), so this is a
generally-applicable normalization, not a fix aimed at one fixture.

Known limitation, not solved here: a model can still evade this check by
paraphrasing MORE than dropping comment markers -- the same real MR !25
data also has a case where the echo dropped a '...' line-elision marker
mid-quote, which this normalization does not attempt to compensate for
(seemed likely to be overfitting to one artifact rather than a
generalizable pattern, unlike comment-marker stripping). "Near-exact," not
"any paraphrase" -- see this module's own test suite for the exact
boundary of what is and isn't caught, verified against real data rather
than asserted.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

_MIN_EVIDENCE_LENGTH_FOR_MATCH = 20
_COMMENT_LEADER_RE = re.compile(r"^\s*#\s?", re.MULTILINE)


def _collapse_whitespace(text: str) -> str:
    """Collapse all whitespace runs to a single space, then strip ends.

    Same logic as dedupe_findings.py's own _collapse_whitespace (see that
    module's docstring for the original whitespace-variant bug this fixes)
    -- duplicated, not imported, per this module's own docstring.
    """
    return re.sub(r"\s+", " ", text).strip()


def _normalize_for_provenance_match(text: str) -> str:
    """Strip '#' comment leaders per line, then collapse whitespace.

    Order matters: comment-leader stripping runs on the ORIGINAL
    line-structured text (it anchors on '^' per line via re.MULTILINE),
    before whitespace collapsing destroys the line boundaries it depends
    on. See this module's own docstring for why comment-leader stripping
    specifically (not broader fuzzy matching) is worth doing here.
    """
    return _collapse_whitespace(_COMMENT_LEADER_RE.sub("", text))


def check_evidence_provenance(findings: list[dict], prior_review_md: str, full_diff: str) -> list[dict]:
    """Flag findings whose evidence echoes prior-round content instead of the current diff.

    No-op-shaped (every finding still gets prior_context_echoed: False) when
    prior_review_md is empty (first-round reviews, nothing injected to
    echo) -- doesn't short-circuit early, so the output shape is uniform
    regardless of round, matching this codebase's established "stable
    per-finding shape" principle (see prepare_findings_for_verification.py's
    module docstring for the same reasoning applied to evidence_status).
    """
    normalized_prior = _normalize_for_provenance_match(prior_review_md)
    normalized_diff = _normalize_for_provenance_match(full_diff)

    checked = []
    for finding in findings:
        # `or ""` before str(): a finding with evidence explicitly set to
        # None must normalize to the empty string, not the 4-character
        # string "None" -- str(finding.get("evidence", "")) would silently
        # produce the latter for a None value (the key present, not
        # missing, so the default never applies), which is long enough to
        # slip past _MIN_EVIDENCE_LENGTH_FOR_MATCH on a coincidental match.
        normalized_evidence = _normalize_for_provenance_match(str(finding.get("evidence") or ""))
        echoed = (
            len(normalized_evidence) >= _MIN_EVIDENCE_LENGTH_FOR_MATCH
            and normalized_evidence in normalized_prior
            and normalized_evidence not in normalized_diff
        )
        if echoed:
            checked.append(
                {
                    **finding,
                    "evidence_status": "refuted",
                    "verification_evidence": finding.get("evidence", ""),
                    "verification_rationale": (
                        "Automatically flagged by check_evidence_provenance, not model "
                        "self-report: this finding's evidence text matches the injected "
                        "prior-round review content verbatim but does not independently "
                        "appear in the current diff -- likely a stale or hallucinated "
                        "citation echoed from history rather than freshly derived from "
                        "this diff."
                    ),
                    "prior_context_echoed": True,
                }
            )
        else:
            # evidence_status: None explicitly, not omitted -- verified live
            # (tests/test_merge_dedup.yml) that Ansible's Jinja rejectattr
            # raises on a genuinely MISSING dict key rather than treating it
            # as falsy ("'dict object' has no attribute 'evidence_status'"),
            # unlike a key present with value None. merge.yml's own score/
            # verdict computation calls rejectattr('evidence_status',
            # 'equalto', 'refuted') on combined_findings right after this
            # plugin runs, so every finding needs the key present -- same
            # "always present, sentinel None" discipline
            # prepare_findings_for_verification.py's own module docstring
            # already documents for this exact field.
            checked.append({**finding, "evidence_status": None, "prior_context_echoed": False})
    return checked


class ActionModule(ActionBase):
    """Flag findings whose evidence echoes injected prior-round content instead of the current diff."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("findings", "prior_review_md", "full_diff"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        findings = self._task.args.get("findings")
        prior_review_md = self._task.args.get("prior_review_md")
        full_diff = self._task.args.get("full_diff")
        if findings is None or prior_review_md is None or full_diff is None:
            result["failed"] = True
            result["msg"] = "check_evidence_provenance requires 'findings', 'prior_review_md', and 'full_diff' arguments"
            return result

        result["changed"] = False
        result["findings"] = check_evidence_provenance(findings, prior_review_md, full_diff)
        return result
