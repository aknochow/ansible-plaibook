# -*- coding: utf-8 -*-
"""Shared action plugin: resolve a verify-turn continuity claim against the prior round's findings.

implement-continues-finding-id
(handoff.ansible-plaibook-stable-finding-id.yaml): verify_finding.yml's own
verify_turn.yml loop asks the model, as part of its single report_verdict
call, whether this finding continues a specific prior-round finding
(continues_finding_id, shown alongside file/line/description-only
prior_round_findings -- see verify_agent_prompt.j2). This plugin decides,
from that raw claim, whether an independent continuity AUDIT (a second,
adversarial, fresh-conversation call -- verify_continuity_audit.yml)
should even run, and if so, resolves which actual prior finding was
claimed.

Takes claim_input (report_verdict's raw tool-call `input` dict, or a
synthetic {"continues_finding_id": None} on a stall/turn-exhaustion
fallback) as ONE whole dict, not continues_finding_id as its own scalar
arg -- deliberately, matching merge_verify_result.py's own claim_input
arg (see that plugin's module docstring for the full "why a whole dict,
not a nullable scalar" rationale: a `{{ }}` template whose own top-level
rendered result is a bare None gets finalized to the string "" by this
repo's non-native templating, confirmed via a standalone Templar probe,
regardless of how many dots or filters preceded it). Plain Python
dict.get() here has no such quirk.

should_audit is False (never runs the audit call) in two distinct
cases, deliberately not distinguished in this plugin's own output --
merge_verify_result.py's else-branch treats both identically anyway
(continuity_status: "refuted" whenever no plausible confirmation
exists):
  1. No claim at all (continues_finding_id is None).
  2. A claim was made, but its id doesn't match anything in
     prior_round_findings -- a hallucinated or stale id. Nothing real
     exists to show an auditor, so the audit call itself is skipped
     rather than run against a fabricated comparison target.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase


def resolve_continuity_claim(claim_input: dict, prior_round_findings: list[dict]) -> dict:
    continues_finding_id = claim_input.get("continues_finding_id")
    if continues_finding_id is None:
        return {"should_audit": False, "claimed_prior_finding": None}

    matches = [f for f in prior_round_findings if f.get("finding_id") == continues_finding_id]
    if not matches:
        return {"should_audit": False, "claimed_prior_finding": None}

    return {"should_audit": True, "claimed_prior_finding": matches[0]}


class ActionModule(ActionBase):
    """Resolve a claimed continuity id against the prior round's findings -- real Python, no Jinja leaf-None risk."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("claim_input", "prior_round_findings"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        # is None, not falsy -- claim_input can legitimately be {} (no
        # claim), prior_round_findings can legitimately be [] (no prior
        # round, or none carried an id) -- neither is ever None itself.
        missing = sorted(arg for arg in self._VALID_ARGS if self._task.args.get(arg) is None)
        if missing:
            result["failed"] = True
            result["msg"] = f"resolve_continuity_claim requires all of {sorted(self._VALID_ARGS)}; missing: {missing}"
            return result

        resolved = resolve_continuity_claim(
            claim_input=self._task.args["claim_input"],
            prior_round_findings=self._task.args["prior_round_findings"],
        )

        result["changed"] = False
        result["should_audit"] = resolved["should_audit"]
        result["claimed_prior_finding"] = resolved["claimed_prior_finding"]
        return result
