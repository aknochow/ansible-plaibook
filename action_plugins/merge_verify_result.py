# -*- coding: utf-8 -*-
"""Shared action plugin: merge one finding's verify verdict back into the findings list.

Fifth port for the action-plugin migration roadmap
(handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml, item
port-verify-finding-bookkeeping-cluster), piece (3) of that item: the
per-finding merge-back verify_finding.yml runs once per scoped finding,
inside the verification turn loop, matching on _verify_index.

Equivalence verified against the real, unmodified Jinja expression via
Ansible's real Templar (not jinja2_native) -- a single per-item
conditional expression, no groupby/sort/accumulator-across-items
behavior, so a direct render is a faithful equivalence check. See
test_merge_verify_result.py for the captured baseline.

Original Jinja (verify_finding.yml):
    {{ (item | combine({
         'evidence_status': verify_result_status,
         'verification_evidence': verify_result_evidence,
         'verification_rationale': verify_result_rationale,
         'requires_execution': verify_result_requires_execution
       }))
       if item._verify_index == verify_target_finding._verify_index
       else item }}

Deliberate addition, not in the original Jinja: FindingNotFoundError if
no finding in the list has a matching _verify_index. The original
silently no-ops (Jinja's Undefined-vs-int comparison is just False, not
an error) -- can't actually happen in real use, since
prepare_findings_for_verification.py always assigns _verify_index to
every finding before this ever runs, and verify_target_finding is
always one of the very findings being searched. Same "fail loudly on a
structural invariant violation instead of a silent no-op" rationale as
the InvalidFindingError guards in compute_review_scores.py/
dedupe_findings.py -- a silently-dropped verify verdict would otherwise
be a confusing, hard-to-notice bug.

Real gap caught by running tests/test_verify_pass.yml for real (not by
pytest, which only exercises the pure function/ActionModule directly
with already-native Python types): verify_finding.yml passes
verify_index as `{{ verify_target_finding._verify_index }}` -- a DOT
ACCESS on a dict attribute, not a bare variable reference. Confirmed by
a standalone probe against a real Ansible Templar that this repo's
non-native templating (jinja2_native=false) only recovers a scalar's
native type for a template that is EXACTLY `{{ bare_variable }}` --
`{{ some_dict.some_int_key }}` renders as the STRING "0", not the int
0, even though `{{ some_dict.some_bool_key }}` DOES come back as a
native bool (an inconsistency in Ansible's own reconversion logic, not
something to rely on either way). Every OTHER arg here is either
already meant to be a string (evidence_status et al.) or reaches this
plugin via a bare variable reference (requires_execution), so
verify_index is the only one actually at risk -- cast defensively
below rather than trust the caller's Jinja shape.

Axis 1 extension (expand-verify-yml-severity-check,
handoff.ansible-plaibook-verify-yml-scope-expansion.yaml): also merges in
suggested_severity (verify_finding.yml's call to
compute_suggested_severity.py) and the 4 raw sub-answers it was derived
from, for display/audit in findings.md. Per
decide-severity-rubric-scope-and-score-impact's DECIDED resolution, a
disputed severity DOES change the finding's actual `severity` field
(and therefore compute_review_scores.py's/verify.yml's regrouping,
which both read `finding['severity']` directly) -- re-deriving severity
and then not using the corrected value would defeat the point of
checking it at all. The ORIGINAL lens-assigned severity is preserved in
`original_severity`, never silently dropped -- this overwrites the
scoring-relevant field, it doesn't erase the fact that a dispute
happened.

Axis 4 extension (expand-verify-yml-reachability-check, same handoff):
also merges in used_static_reachability_trace -- sourced by
verify_turn.yml/verify_finding.yml from the deterministic
verify_reachability_tool_used tracking fact, not from the model's own
report_verdict output (see trace_reachability.py's module docstring for
why this is intentionally NOT part of verify_verdict_schema).

implement-continues-finding-id extension
(handoff.ansible-plaibook-stable-finding-id.yaml): resolves this finding's
FINAL finding_id and continuity_status from two whole-dict inputs
rather than individual scalar args -- claim_input (report_verdict's own
raw tool-call input, or a synthetic {"continues_finding_id": None} on a
stall/exhaustion fallback path) and audit_result ({} if no audit ran,
otherwise {"verdict": ..., "rationale": ...} from
verify_continuity_audit.yml). This is deliberately NOT the same shape
as evidence_status/rationale/etc. above (plain scalar args) -- verified
live, via a standalone Templar probe, that continues_finding_id: null
is NOT safely passable as its own scalar arg: the moment a `{{ }}`
template's own FINAL, top-level rendered result is a bare None (however
many dots preceded it, and regardless of whether a `| default(...)`
filter is involved), Ansible's non-native templating finalizes it to
the STRING "" -- not preserved as real None -- so any caller-side
`{{ some_result.continues_finding_id }}` would have silently corrupted
every "no claim" case into an indistinguishable-from-a-real-id empty
string. Passing the WHOLE containing dict through untouched (never
further dot-accessed by Jinja after the one hop that produces it) and
extracting the leaf value here, in real Python via plain dict.get(),
sidesteps this entirely -- Python dict access has no such quirk.

Three-way continuity outcome, computed here rather than pre-branched in
Jinja before this call -- same "non-trivial branching belongs in
Python, not scattered set_fact ternaries" posture as severity_status's
disputed-vs-confirmed resolution just above:

- No claim (claim_input's continues_finding_id is None): continuity_status
  "not-claimed", finding keeps its own freshly-assigned finding_id
  (prepare_findings_for_verification.py's uuid, read directly off the
  matched finding record -- not re-passed as a separate argument).
- Claimed and the audit found it plausible (audit_result['verdict'] ==
  'plausible'): continuity_status "confirmed", finding_id is
  OVERWRITTEN with the claimed prior id -- this is the one case where
  identity actually carries across rounds.
- Claimed but the audit found it implausible, or audit_result is {}
  (verify_finding.yml only runs the audit when a claim matched a real
  prior-round finding -- see resolve_continuity_claim.py -- so an empty
  audit_result here means either no claim or an unmatched/hallucinated
  claimed id; both are treated the same as implausible rather than
  silently trusting an absent verdict): continuity_status "refuted",
  finding keeps its own id, same as the no-claim case. continues_finding_id
  itself is always echoed through unchanged (even when refuted) --
  keep-and-annotate, the same transparency precedent evidence_status:
  refuted and severity_status: disputed both already established,
  rather than silently dropping a claim that didn't hold up.

_FINDING_ID_PATTERN validates the "confirmed" branch's inherited id
matches prepare_findings_for_verification.py's own uuid4().hex[:12]
shape before trusting it as the new finding_id. Dogfood review raised
this: resolve_continuity_claim.py already exact-matches
continues_finding_id against a REAL prior-round finding_id before the
audit ever runs (an arbitrary/injected string can't reach "confirmed"
at all -- it would fail that match, should_audit would be False, and
this function's own no-claim/refuted branches would fire instead,
never this one) -- traced directly, not assumed, so the specific
"a fabricated string becomes finding_id" exploit path this addresses
does not exist today. Added anyway: this makes the safety property a
structural, enforced invariant rather than something that happens to
hold only because of how resolve_continuity_claim.py's matching
currently works -- exactly the "verify the invariant, don't reason
about the current mechanism" posture
adopt-total-symlink-removal-with-verify-before-exec (ansible-plaibook MR !47)
already established for this same codebase. A non-matching value is
treated the same as "refuted" (own id kept) rather than raised as an
error -- it's a defensive backstop for an invariant that shouldn't be
violated through normal operation, not a structural violation worth
aborting the whole verify pass over.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

_FINDING_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")


class FindingNotFoundError(ValueError):
    """Raised when no finding in the list has the given verify_index."""


def merge_verify_result(
    findings: list[dict],
    verify_index: int,
    evidence_status: str,
    verification_evidence: str,
    verification_rationale: str,
    requires_execution: bool,
    suggested_severity: str,
    reachability: str,
    trust_boundary: str,
    worst_outcome_category: str,
    silent_failure: bool | str,
    used_static_reachability_trace: bool,
    claim_input: dict,
    audit_result: dict,
) -> list[dict]:
    # int(), not assumed-native -- see module docstring's "Real gap"
    # note: dot-accessed integers don't reliably arrive pre-coerced.
    verify_index = int(verify_index)

    # Same defensive coercion as compute_suggested_severity.py, for the
    # same reason -- this finding's own silent_failure value is stored
    # directly into the persisted finding dict below, so it should be
    # coerced by the same rule wherever it's consumed, not just where a
    # bad value would have visibly broken something (the severity
    # computation) versus just persisted a wrong type silently.
    if isinstance(silent_failure, str):
        silent_failure = silent_failure.strip().lower() in ("true", "1", "yes")

    continues_finding_id = claim_input.get("continues_finding_id")
    audit_verdict = audit_result.get("verdict")
    audit_rationale = audit_result.get("rationale", "")

    found = False
    merged = []
    for finding in findings:
        if finding.get("_verify_index") == verify_index:
            found = True
            original_severity = finding["severity"]
            own_finding_id = finding.get("finding_id")
            if continues_finding_id is None:
                continuity_status = "not-claimed"
                final_finding_id = own_finding_id
            elif audit_verdict == "plausible" and _FINDING_ID_PATTERN.fullmatch(continues_finding_id):
                continuity_status = "confirmed"
                final_finding_id = continues_finding_id
            else:
                continuity_status = "refuted"
                final_finding_id = own_finding_id
            merged.append(
                {
                    **finding,
                    "evidence_status": evidence_status,
                    "verification_evidence": verification_evidence,
                    "verification_rationale": verification_rationale,
                    "requires_execution": requires_execution,
                    "severity": suggested_severity,
                    "original_severity": original_severity,
                    "severity_status": "disputed" if suggested_severity != original_severity else "confirmed",
                    "reachability": reachability,
                    "trust_boundary": trust_boundary,
                    "worst_outcome_category": worst_outcome_category,
                    "silent_failure": silent_failure,
                    "used_static_reachability_trace": used_static_reachability_trace,
                    "finding_id": final_finding_id,
                    "continues_finding_id": continues_finding_id,
                    "continuity_status": continuity_status,
                    "continuity_rationale": audit_rationale,
                }
            )
        else:
            merged.append(finding)

    if not found:
        raise FindingNotFoundError(f"no finding has _verify_index == {verify_index!r}")
    return merged


class ActionModule(ActionBase):
    """Merge one finding's verify verdict back by _verify_index -- real Python instead of a loop/accumulator."""

    _requires_connection = False
    _VALID_ARGS = frozenset(
        (
            "findings",
            "verify_index",
            "evidence_status",
            "verification_evidence",
            "verification_rationale",
            "requires_execution",
            "suggested_severity",
            "reachability",
            "trust_boundary",
            "worst_outcome_category",
            "silent_failure",
            "used_static_reachability_trace",
            "claim_input",
            "audit_result",
        )
    )

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        # is None, not falsy -- requires_execution is a legitimate False,
        # evidence_status/the two text fields can legitimately be "".
        # claim_input/audit_result are always AT LEAST an empty dict
        # (never None themselves -- only a KEY inside claim_input can be
        # None, which this check never looks at), so they need no
        # special-casing here.
        missing = sorted(arg for arg in self._VALID_ARGS if self._task.args.get(arg) is None)
        if missing:
            result["failed"] = True
            result["msg"] = f"merge_verify_result requires all of {sorted(self._VALID_ARGS)}; missing: {missing}"
            return result

        try:
            merged = merge_verify_result(
                findings=self._task.args["findings"],
                verify_index=self._task.args["verify_index"],
                evidence_status=self._task.args["evidence_status"],
                verification_evidence=self._task.args["verification_evidence"],
                verification_rationale=self._task.args["verification_rationale"],
                requires_execution=self._task.args["requires_execution"],
                suggested_severity=self._task.args["suggested_severity"],
                reachability=self._task.args["reachability"],
                trust_boundary=self._task.args["trust_boundary"],
                worst_outcome_category=self._task.args["worst_outcome_category"],
                silent_failure=self._task.args["silent_failure"],
                used_static_reachability_trace=self._task.args["used_static_reachability_trace"],
                claim_input=self._task.args["claim_input"],
                audit_result=self._task.args["audit_result"],
            )
        except FindingNotFoundError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result

        result["changed"] = False
        result["findings"] = merged
        return result
