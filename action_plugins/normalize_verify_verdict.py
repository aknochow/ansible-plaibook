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

A real verdict with an incomplete severity answer keeps its status, and
the finding keeps its severity. compute_suggested_severity.py derives
severity from all four axis fields, so filling a missing one with a
default can compute Nit for a verified Critical, and a missing
silent_failure loses its one-level bump. Only a complete, valid answer
counts as assessed.
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

_VALID_AXIS_VALUES = {
    "reachability": frozenset(("realistic", "requires-unusual-config", "theoretical-only")),
    "trust_boundary": frozenset(("crosses-trust-boundary", "stays-within-trusted-input", "unclear")),
    "worst_outcome_category": frozenset(
        (
            "crash-dos",
            "data-corruption",
            "unauthorized-read",
            "unauthorized-write-or-rce",
            "availability-degradation",
            "cosmetic-style",
        )
    ),
}

_SILENT_FAILURE_STRINGS = {"true": True, "false": False}

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


def _coerce_silent_failure(value):
    """Return `value` as a bool, or None if it's neither a bool nor its string form."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _SILENT_FAILURE_STRINGS.get(value.strip().lower())
    return None


def _assessed_axes(verdict: Mapping) -> dict | None:
    """Return the four axis values if all are present and valid, otherwise None."""
    for field, valid_values in _VALID_AXIS_VALUES.items():
        value = verdict.get(field)
        if not isinstance(value, str) or value not in valid_values:
            return None
    silent_failure = _coerce_silent_failure(verdict.get("silent_failure"))
    if silent_failure is None:
        return None
    return {**{field: verdict[field] for field in _VALID_AXIS_VALUES}, "silent_failure": silent_failure}


def normalize_verify_verdict(verdict) -> dict:
    """Return the verify_result_* values to use for one report_verdict input."""
    verdict = verdict if isinstance(verdict, Mapping) else {}
    evidence = verdict.get("evidence")
    rationale = verdict.get("rationale")
    status = verdict.get("evidence_status")

    if (
        not isinstance(status, str)
        or status not in _VALID_STATUSES
        or is_placeholder_text(evidence)
        or is_placeholder_text(rationale)
    ):
        return {
            "status": "inconclusive",
            "evidence": "" if is_placeholder_text(evidence) else evidence,
            "rationale": UNSUPPORTED_RATIONALE,
            "requires_execution": False,
            "severity_assessed": False,
            **_UNASSESSED_AXES,
        }

    axes = _assessed_axes(verdict)
    return {
        "status": status,
        "evidence": evidence,
        "rationale": rationale,
        "requires_execution": verdict.get("requires_execution", False),
        "severity_assessed": axes is not None,
        **(axes if axes is not None else _UNASSESSED_AXES),
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
