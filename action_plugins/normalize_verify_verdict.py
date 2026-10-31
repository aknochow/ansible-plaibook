# -*- coding: utf-8 -*-
"""Shared action plugin: decide how far a report_verdict call can be trusted.

Forcing the tool call on a verifier's final turn guarantees a call, not
a considered answer. A forced call can fill the required fields with
filler and still say `verified`, and that verdict would otherwise move
the finding's severity and the review's verdict. A verdict without real
evidence and a real rationale is treated as `inconclusive`, with the
finding's original severity kept.

Placeholder matching is on the whole normalized field, never a
substring, so a rationale that merely mentions one of these words keeps
its verdict.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from ansible.plugins.action import ActionBase

_VALID_STATUSES = frozenset(("verified", "refuted", "inconclusive"))

_PLACEHOLDERS = frozenset(
    (
        "dummy",
        "evidence",
        "evidence here",
        "fixme",
        "lorem ipsum",
        "n a",
        "na",
        "none",
        "not applicable",
        "null",
        "placeholder",
        "placeholder evidence",
        "placeholder rationale",
        "placeholder text",
        "placeholder value",
        "rationale",
        "rationale here",
        "stub",
        "tbd",
        "todo",
        "undefined",
        "unknown",
        "x",
        "xxx",
    )
)

_NON_WORD_RUN = re.compile(r"[\W_]+")

UNSUPPORTED_RATIONALE = (
    "Verification agent returned empty or placeholder evidence or rationale, "
    "so this verdict was treated as inconclusive."
)

# Same inert values as verify_turn.yml's other no-verdict paths. Nothing
# computes a severity from them, because severity_assessed is false.
_UNASSESSED_AXES = {
    "reachability": "theoretical-only",
    "trust_boundary": "unclear",
    "worst_outcome_category": "cosmetic-style",
    "silent_failure": False,
}


def is_placeholder_text(value) -> bool:
    """Return True if `value` is not text, is empty, or is only a known filler value."""
    if not isinstance(value, str):
        return True
    return _NON_WORD_RUN.sub(" ", value).strip().lower() in _PLACEHOLDERS | {""}


def normalize_verify_verdict(verdict) -> dict:
    """Return the verify_result_* values to use for one report_verdict input."""
    verdict = verdict if isinstance(verdict, Mapping) else {}
    evidence = verdict.get("evidence")
    rationale = verdict.get("rationale")
    status = verdict.get("evidence_status")

    if status not in _VALID_STATUSES or is_placeholder_text(evidence) or is_placeholder_text(rationale):
        return {
            "status": "inconclusive",
            "evidence": "" if is_placeholder_text(evidence) else evidence,
            "rationale": UNSUPPORTED_RATIONALE,
            "requires_execution": False,
            "severity_assessed": False,
            **_UNASSESSED_AXES,
        }

    return {
        "status": status,
        "evidence": evidence,
        "rationale": rationale,
        "requires_execution": verdict.get("requires_execution", False),
        "severity_assessed": True,
        "reachability": verdict.get("reachability", _UNASSESSED_AXES["reachability"]),
        "trust_boundary": verdict.get("trust_boundary", _UNASSESSED_AXES["trust_boundary"]),
        "worst_outcome_category": verdict.get("worst_outcome_category", _UNASSESSED_AXES["worst_outcome_category"]),
        "silent_failure": verdict.get("silent_failure", _UNASSESSED_AXES["silent_failure"]),
    }


class ActionModule(ActionBase):
    """Normalize one report_verdict input -- real Python instead of set_fact defaults."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("verdict",))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        if "verdict" not in self._task.args:
            result["failed"] = True
            result["msg"] = "normalize_verify_verdict requires a 'verdict' argument"
            return result

        result["changed"] = False
        result.update(normalize_verify_verdict(self._task.args["verdict"]))
        return result
