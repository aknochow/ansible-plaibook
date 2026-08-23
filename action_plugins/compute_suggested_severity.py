# -*- coding: utf-8 -*-
"""Shared action plugin: derive a suggested severity from 4 categorical sub-answers.

verify.yml's evidence_status check confirms a finding is factually
true, but never whether its assigned severity is correct: a finding
can be fully verified and still carry the wrong severity, silently
skewing compute_review_scores.py's severity-fed formula. Re-asking the
lens's own holistic "what's the worst realistic outcome" question a
second time would just be a second correlated guess, not an
independent check, so this decomposes that judgment into 4 narrow,
independently-answerable categorical sub-questions (see
verify_verdict_schema in roles/review/vars/main.yml) and derives the
severity deterministically via the fixed table below.

Design:
1. Base severity comes from worst_outcome_category alone: the
   dimension closest to "how bad is this if it happens."
2. reachability can only ever move severity down, never up: a realistic
   RCE is exactly as bad as worst_outcome_category says, but a
   theoretical-only one is much less urgent right now regardless of how
   bad it'd be if it ever fired.
3. trust_boundary can only ever move severity down, never up, and only
   when the input stays within this pipeline's own trusted operator/
   config surface. Crossing a trust boundary doesn't make an outcome
   worse than worst_outcome_category already says; it's already priced
   in there.
4. silent_failure applies last, as a one-level bump, matching the
   existing rubric's "silent failure bumps one level" rule, applied to
   whatever base the first 3 axes already produced.
5. Clamped to the closed [Nit, Critical] range throughout.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase

_LEVELS = ("Nit", "Minor", "Major", "Critical")

_BASE_SEVERITY_BY_OUTCOME = {
    "unauthorized-write-or-rce": "Critical",
    "data-corruption": "Critical",
    "unauthorized-read": "Major",
    "crash-dos": "Major",
    "availability-degradation": "Minor",
    "cosmetic-style": "Nit",
}

# How many levels DOWN reachability discounts the base severity by.
# Never a positive/upward adjustment -- see module docstring point 2.
_REACHABILITY_DISCOUNT = {
    "realistic": 0,
    "requires-unusual-config": 1,
    "theoretical-only": 2,
}

# How many levels DOWN staying within the trusted surface discounts the
# base severity by. 'unclear' gets no discount, deliberately -- genuine
# uncertainty about the trust boundary isn't rewarded with a free
# downgrade. See module docstring point 3.
_TRUST_BOUNDARY_DISCOUNT = {
    "crosses-trust-boundary": 0,
    "stays-within-trusted-input": 1,
    "unclear": 0,
}


class InvalidSeverityInputsError(ValueError):
    """Raised when one of the 4 categorical inputs isn't a recognized value.

    These are LLM-authored, schema-constrained values (verify_verdict_schema
    in roles/review/vars/main.yml pins each to a closed enum), but model
    output shouldn't be trusted blindly. Fail loudly here rather than
    silently fall back to a default that could mask a real schema drift.
    """


def compute_suggested_severity(
    reachability: str,
    trust_boundary: str,
    worst_outcome_category: str,
    silent_failure: bool | str,
) -> str:
    # Coerced defensively rather than assumed-native: Ansible's non-native
    # Jinja templating can stringify a dot-accessed value passed through a
    # quoted `{{ }}` task arg. The coercion is a no-op on an already-native
    # bool, so it costs nothing even where it isn't currently needed.
    if isinstance(silent_failure, str):
        silent_failure = silent_failure.strip().lower() in ("true", "1", "yes")

    if worst_outcome_category not in _BASE_SEVERITY_BY_OUTCOME:
        raise InvalidSeverityInputsError(
            f"unrecognized worst_outcome_category: {worst_outcome_category!r} "
            f"(known: {sorted(_BASE_SEVERITY_BY_OUTCOME)})"
        )
    if reachability not in _REACHABILITY_DISCOUNT:
        raise InvalidSeverityInputsError(
            f"unrecognized reachability: {reachability!r} (known: {sorted(_REACHABILITY_DISCOUNT)})"
        )
    if trust_boundary not in _TRUST_BOUNDARY_DISCOUNT:
        raise InvalidSeverityInputsError(
            f"unrecognized trust_boundary: {trust_boundary!r} (known: {sorted(_TRUST_BOUNDARY_DISCOUNT)})"
        )

    level = _LEVELS.index(_BASE_SEVERITY_BY_OUTCOME[worst_outcome_category])
    level -= _REACHABILITY_DISCOUNT[reachability]
    level -= _TRUST_BOUNDARY_DISCOUNT[trust_boundary]
    level = max(level, 0)
    if silent_failure:
        level += 1
    level = min(level, len(_LEVELS) - 1)
    return _LEVELS[level]


class ActionModule(ActionBase):
    """Derive suggested_severity from 4 categorical sub-answers -- a fixed table, not a second holistic guess."""

    _requires_connection = False
    _VALID_ARGS = frozenset(
        (
            "reachability",
            "trust_boundary",
            "worst_outcome_category",
            "silent_failure",
        )
    )

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        missing = sorted(arg for arg in self._VALID_ARGS if self._task.args.get(arg) is None)
        if missing:
            result["failed"] = True
            result["msg"] = f"compute_suggested_severity requires all of {sorted(self._VALID_ARGS)}; missing: {missing}"
            return result

        try:
            suggested_severity = compute_suggested_severity(
                reachability=self._task.args["reachability"],
                trust_boundary=self._task.args["trust_boundary"],
                worst_outcome_category=self._task.args["worst_outcome_category"],
                silent_failure=self._task.args["silent_failure"],
            )
        except InvalidSeverityInputsError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result

        result["changed"] = False
        result["suggested_severity"] = suggested_severity
        return result
