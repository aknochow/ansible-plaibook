# -*- coding: utf-8 -*-
"""Shared action plugin: drop self-refuted findings.

Lives in the repo-root action_plugins/ (registered via ansible.cfg's
action_plugins setting), not a role-local directory: a role-local
plugin only resolves while its specific role is in play, and this
plugin needs to keep resolving regardless of role structure changes.

Review-lens models occasionally talk themselves out of a finding in
prose (in the evidence, fix, or description field) without actually
removing it from the findings array. The patterns below catch the
real-world wordings this has taken in practice; new wordings are added
here as they're caught.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

EVIDENCE_REFUTED_PATTERN = r"(?i)refut"

# Five independent signals: an explicit "refuted"/"dropping" admission, a
# "no fix/change/action needed" hedge (filler-word tolerant), the model
# asserting the existing code is already correct, or a materiality hedge
# saying the finding isn't actionable enough to warrant a change,
# distinct from "no fix needed": that phrasing concedes a fix would be
# one thing to do, just not a worthwhile one.
FIX_REFUTED_PATTERN = (
    r"(?i)refut|dropping\b|drop(?:ped|ping)? (this|the) finding|"
    r"^\s*(n/?a\b|not applicable)|"
    r"no\s+(?:\w+\s+){0,2}(?:fix|change|action)\w*\s*(?:is\s+|are\s+|was\s+)?(?:needed|required)|"
    r"\b(?:is|are)\s+(?:actually\s+|already\s+)?correct\b|"
    r"not\s+actionable\b"
)

# Catches a self-retraction that lives entirely in the description field,
# where the fix field alone still reads like a normal, actionable fix.
DESCRIPTION_REFUTED_PATTERN = r"(?i)no actual (bug|issue|vulnerability|problem)\b"


def is_self_refuted(finding):
    """Return True if `finding` matches a known self-refutation signal."""
    evidence = finding.get("evidence") or ""
    fix = finding.get("fix") or ""
    description = finding.get("description") or ""
    return bool(
        re.search(EVIDENCE_REFUTED_PATTERN, evidence)
        or re.search(FIX_REFUTED_PATTERN, fix)
        or re.search(DESCRIPTION_REFUTED_PATTERN, description)
    )


def filter_self_refuted_findings(findings):
    """Return `findings` with self-refuted entries dropped, order preserved."""
    return [finding for finding in findings if not is_self_refuted(finding)]


class ActionModule(ActionBase):
    """Drop self-refuted findings -- real Python instead of a Jinja rejectattr chain."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("findings",))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        findings = self._task.args.get("findings")
        if findings is None:
            result["failed"] = True
            result["msg"] = "filter_self_refuted_findings requires a 'findings' argument"
            return result

        survivors = filter_self_refuted_findings(findings)

        result["changed"] = False
        result["findings"] = survivors
        result["dropped_count"] = len(findings) - len(survivors)
        return result
