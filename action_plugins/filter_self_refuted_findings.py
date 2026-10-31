# -*- coding: utf-8 -*-
"""Shared action plugin: drop self-refuted findings.

Lives in the repo-root action_plugins/ (registered via ansible.cfg's
action_plugins setting), not a role-local directory: a role-local
plugin only resolves while its specific role is in play, and this
plugin needs to keep resolving regardless of role structure changes.

Review-lens models occasionally talk themselves out of a finding in
prose (in the fix or description field) without actually removing it
from the findings array. The patterns below catch explicit retractions;
new wordings are added here as they're caught.

The patterns are deliberately narrow, and anchored to where a retraction
actually appears, because a match deletes the finding before anything
else can check it. Evidence is never matched: it quotes the reviewed
code, so it can contain any word. Fix and description text can use the
same vocabulary to describe real work ("Stop dropping the auth header",
"so the path is correct"), which broader matching dropped by mistake. A
retraction these patterns miss still reaches the verify pass.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

# Where a retraction's own clause can end: the end of the field, or
# punctuation that closes the clause. A word after it ("No change needed
# in the callers") means it's scoping the sentence, not retracting.
_CLAUSE_END = r"\s*(?:$|[.;:!—–]|-\s)"
_REEXAMINED = r"(?:after|on|upon)\s+re-?\w+"

# Six independent signals, each an explicit retraction: a bare "N/A", a
# "no fix/change/action needed" opening (filler-word tolerant), a leading
# "refuted", a closing "dropping"/"dropping this finding", an opening
# claim that the existing code is already correct, or a materiality hedge
# conceding a fix isn't worthwhile.
FIX_REFUTED_PATTERN = (
    r"(?i)^\s*(?:n/?a|not\s+applicable)" + _CLAUSE_END + r"|"
    r"^\s*(?:" + _REEXAMINED + r",?\s+)?"
    r"no\s+(?:\w+\s+){0,2}(?:fix|change|action)\w*\s*(?:is\s+|are\s+|was\s+)?(?:needed|required)(?:\s+here)?"
    + _CLAUSE_END + r"|"
    r"^\s*(?:(?:this\s+)?(?:finding|claim|issue)\s+(?:is\s+|was\s+)?)?(?:self-?)?refuted(?:\s+" + _REEXAMINED + r")?"
    + _CLAUSE_END + r"|"
    r"(?:^|[.;!?]\s+|\s-\s*|[—–]\s*)(?:dropping|(?:drop|dropped|dropping)\s+(?:this|the)\s+finding)\s*[.!]?\s*$|"
    r"^\s*(?:the\s+)?(?:(?:existing|current|original)\s+)?(?:code|logic|behaviou?r|implementation|refactor)\s+"
    r"(?:here\s+)?(?:is|are)\s+(?:actually\s+|already\s+)?correct(?:\s+(?:as\s+(?:is|written)|" + _REEXAMINED + r"))?"
    + _CLAUSE_END + r"|"
    r"^\s*(?:this\s+is\s+)?not\s+actionable\b"
)

# Catches a self-retraction that lives entirely in the description field,
# where the fix field alone still reads like a normal, actionable fix.
# Has to open a sentence: mid-sentence, "no actual issue" is usually
# describing something else ("with no actual issue number set").
DESCRIPTION_REFUTED_PATTERN = (
    r"(?i)(?:^|[.;!?]\s+|[—–]\s*)(?:there\s+is\s+|this\s+is\s+)?no\s+actual\s+(?:bug|issue|vulnerability|problem)"
    r"(?:\s+here)?(?:\s+" + _REEXAMINED + r")?" + _CLAUSE_END
)

DROPPED_SUMMARY_MAX_CHARS = 200


def is_self_refuted(finding):
    """Return True if `finding` matches a known self-refutation signal."""
    fix = finding.get("fix") or ""
    description = finding.get("description") or ""
    return bool(re.search(FIX_REFUTED_PATTERN, fix) or re.search(DESCRIPTION_REFUTED_PATTERN, description))


def filter_self_refuted_findings(findings):
    """Return `findings` with self-refuted entries dropped, order preserved."""
    return [finding for finding in findings if not is_self_refuted(finding)]


def summarize_dropped_finding(finding):
    """Return the short record of a dropped finding that callers report."""
    return {
        "file": finding.get("file"),
        "line": finding.get("line"),
        "severity": finding.get("severity"),
        "description": str(finding.get("description") or "")[:DROPPED_SUMMARY_MAX_CHARS],
    }


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
        dropped = [summarize_dropped_finding(finding) for finding in findings if is_self_refuted(finding)]
        result["dropped_count"] = len(dropped)
        result["dropped"] = dropped
        return result
