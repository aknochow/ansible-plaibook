# -*- coding: utf-8 -*-
"""Shared action plugin: default-fill + index + scope-tag a findings list ahead of verify.yml's pass.

Default-filling, indexing, and scope-eligibility tagging always run
back-to-back over the same list in verify.yml, so they're combined into
one plugin call rather than several separate loop/accumulator task
groups.

Verify-eligibility scope is deliberately neither "Critical/Major only"
(misses a truly-Critical finding misclassified down to Minor, which
would never reach verification) nor "every finding" (not worth the
N-findings x M-turns cost multiplier on every Nit): Critical/Major
(`review_verify_severities`) plus Minor findings carrying a heuristic
borderline signal (raised by the Security lens, or touching a
security-sensitive code pattern by keyword).

A stable `finding_id` is assigned here, deterministically, as a finding
becomes verify-eligible, rather than self-reported by a lens. Every
eligible finding gets a fresh id every run; merge_verify_result.py is
the only place that ever overwrites it, and only when an independent
audit confirms a genuine continuation of a specific prior-round
finding. Ineligible findings keep `finding_id: None`.
"""
from __future__ import annotations

import re
import uuid
from typing import Callable

from ansible.plugins.action import ActionBase

_DEFAULTS = {
    "evidence_status": None,
    "requires_execution": False,
    # Sentinel None so findings.md.j2 can check `{% if f.severity_status %}`
    # on every finding, regardless of whether it was ever in scope for
    # verification. No separate suggested_severity default: merge_verify_result.py
    # writes the computed value directly into `severity` itself, so
    # original_severity + severity_status + severity fully capture a dispute.
    "severity_status": None,
    "original_severity": None,
    "used_static_reachability_trace": False,
    # finding_id is overwritten below for eligible findings only;
    # ineligible findings keep the None sentinel.
    "finding_id": None,
    "continues_finding_id": None,
    "continuity_status": None,
    "continuity_rationale": None,
}


class InvalidSecurityPatternError(ValueError):
    """Raised when security_sensitive_pattern isn't a valid regular expression.

    review_verify_security_sensitive_pattern is operator-configured (a
    role default, or an explicit override) -- a syntax error in it is a
    misconfiguration, not attacker input, but should fail with a clear
    message pointing at the actual pattern rather than a raw re.error
    traceback the first time a Minor finding happens to reach this
    check.
    """


# file/description/evidence trace back to diff content and model
# output -- not fully trusted. Bounds the regex engine's worst-case
# input size regardless of pattern complexity, independent of whatever
# pattern review_verify_security_sensitive_pattern gets overridden to
# -- 10KB is far more than needed for a keyword match.
_MAX_HAYSTACK_CHARS = 10_000


def _is_verify_eligible(finding: dict, verify_severities: list[str], compiled_security_pattern: re.Pattern) -> bool:
    if finding.get("severity") in verify_severities:
        return True
    if finding.get("severity") != "Minor":
        return False
    if finding.get("lens") == "Security":
        return True
    haystack = " ".join(str(finding.get(key, "")) for key in ("file", "description", "evidence"))
    return bool(compiled_security_pattern.search(haystack[:_MAX_HAYSTACK_CHARS]))


def prepare_findings_for_verification(
    findings: list[dict],
    verify_severities: list[str],
    security_sensitive_pattern: str,
    id_factory: Callable[[], str] = lambda: uuid.uuid4().hex[:12],
) -> list[dict]:
    """Default-fill, index, and scope-tag every finding ahead of verify.yml's pass.

    `_verify_index` is assigned by `enumerate()` over the input list's
    own order. Merging is shallow: a finding's own nested values are
    shared by reference between input and output.

    Compiles `security_sensitive_pattern` once, up front, rather than
    per finding, so a compile-time `re.error` surfaces as one clear
    `InvalidSecurityPatternError` rather than being masked by an earlier
    finding never reaching the check.

    `id_factory` exists only so tests can assert exact equality against
    a deterministic baseline; `ActionModule.run()` never passes it, so
    real runs always get a genuine uuid4 hex.
    """
    try:
        compiled_security_pattern = re.compile(security_sensitive_pattern, re.IGNORECASE)
    except re.error as exc:
        raise InvalidSecurityPatternError(
            f"security_sensitive_pattern {security_sensitive_pattern!r} is not a valid regular expression: {exc}"
        ) from exc

    prepared = []
    for idx, finding in enumerate(findings):
        merged = {**_DEFAULTS, **finding, "_verify_index": idx}
        merged["_verify_eligible"] = _is_verify_eligible(merged, verify_severities, compiled_security_pattern)
        if merged["_verify_eligible"]:
            merged["finding_id"] = id_factory()
        prepared.append(merged)
    return prepared


class ActionModule(ActionBase):
    """Default-fill, index, and scope-tag a findings list."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("findings", "verify_severities", "security_sensitive_pattern"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        missing = sorted(arg for arg in self._VALID_ARGS if self._task.args.get(arg) is None)
        if missing:
            result["failed"] = True
            result["msg"] = f"prepare_findings_for_verification requires all of {sorted(self._VALID_ARGS)}; missing: {missing}"
            return result

        try:
            findings = prepare_findings_for_verification(
                findings=self._task.args["findings"],
                verify_severities=self._task.args["verify_severities"],
                security_sensitive_pattern=self._task.args["security_sensitive_pattern"],
            )
        except InvalidSecurityPatternError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result

        result["changed"] = False
        result["findings"] = findings
        return result
