# -*- coding: utf-8 -*-
"""Shared action plugin: compute per-lens/overall scores and verdict.

Lives in the repo-root action_plugins/ (registered via ansible.cfg's
action_plugins setting), not roles/review/action_plugins/ --
see the decide-action-plugin-location item in
handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml for why:
role-local action plugins only resolve while their specific role is in
play, and roles/code_review + roles/review have since merged into one
role -- keeping this repo-root regardless, since a role-local plugin
would have stopped resolving entirely once that happened, not just
needed a path rename.

Second port for the action-plugin migration roadmap
(handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml, item
port-score-verdict-computation), unifying three hand-duplicated copies
of the same formula: merge.yml's original computation, verify.yml's
refuted-exclusion recompute, and explore.yml's post-fold-in verdict-only
recompute. All three now call this one plugin instead.

Built directly against the 0-100 scoring scale from the start (per
handoff.ansible-plaibook-100-point-scoring-scale.yaml) -- NOT a port of the
old 0-10 scale followed by a rescale. severity_points is expected to
already be the scaled table (Critical=20, Major=10, Minor=5, Nit=0);
this plugin has no scale constants of its own baked in beyond the
floor/ceiling (10/100), so it stays correct if severity_points is ever
retuned again without needing a second change here.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase

# Must match the lenses review_agent_prompt.j2/security_agent_prompt.j2
# actually instruct the model to report findings for -- if a lens is
# ever added or removed from those prompts without updating this tuple,
# the overall average would silently include a phantom always-100 lens
# or drop a real one, with no error to catch the drift.
LENSES = ("Functionality", "Security", "Quality")


class InvalidFindingError(ValueError):
    """Raised when a finding is missing a required key, or has a severity not in severity_points.

    findings are LLM-authored and schema-constrained (_finding_item in
    roles/review/vars/main.yml requires lens/severity among other
    keys), but this codebase has repeatedly been burned by trusting
    model compliance rather than checking it (see
    filter_self_refuted_findings.py's whole reason for existing) -- fail
    loudly here rather than let a bad or missing key silently crash with
    a raw KeyError, or silently score an unrecognized severity as free
    via a default fallback. Named for the general case (any malformed
    finding), not just the unknown-severity one, since it's also raised
    for missing required keys.
    """


def _compute_lens_score(
    findings: list[dict],
    lens: str,
    severity_points: dict[str, float],
) -> float:
    """max(10, 100 - sum(severity_points of this lens's findings)).

    Private: only safe to call after compute_scores_and_verdict's own
    validation has confirmed every finding has 'severity'/'lens' keys
    with a known severity value -- this function assumes that and will
    raise a raw KeyError otherwise.
    """
    points = sum(severity_points[finding["severity"]] for finding in findings if finding["lens"] == lens)
    return float(max(10, 100 - points))


def compute_verdict(findings: list[dict]) -> str:
    """NEEDS_CHANGES if any surviving Critical/Major finding, else READY_FOR_HUMAN_REVIEW."""
    has_blocking_finding = any(finding["severity"] in ("Critical", "Major") for finding in findings)
    return "NEEDS_CHANGES" if has_blocking_finding else "READY_FOR_HUMAN_REVIEW"


def compute_scores_and_verdict(findings: list[dict], severity_points: dict[str, float]) -> dict[str, float | str]:
    # Checked separately from the unknown-severity validation below: a
    # finding missing the 'severity' or 'lens' key entirely would
    # otherwise raise a raw KeyError from this function's own set-
    # comprehension, or from _compute_lens_score's `finding["lens"]`
    # check, before ever reaching a clear error message. Both keys are
    # equally required by the findings JSON schema (_finding_item in
    # roles/review/vars/main.yml), so both get the same guard.
    for required_key in ("severity", "lens"):
        missing = sum(1 for finding in findings if required_key not in finding)
        if missing:
            raise InvalidFindingError(f"{missing} finding(s) have no '{required_key}' key at all")

    unknown = sorted({finding["severity"] for finding in findings if finding["severity"] not in severity_points})
    if unknown:
        raise InvalidFindingError(
            "finding(s) have a severity not present in severity_points: "
            + ", ".join(unknown)
            + f" (known: {sorted(severity_points)})"
        )

    scores = {lens: _compute_lens_score(findings, lens, severity_points) for lens in LENSES}
    overall = round(sum(scores.values()) / len(LENSES), 1)
    return {
        "score_functionality": scores["Functionality"],
        "score_security": scores["Security"],
        "score_quality": scores["Quality"],
        "score_overall": overall,
        "verdict": compute_verdict(findings),
    }


class ActionModule(ActionBase):
    """Compute scores/verdict from a findings list -- real Python instead of three hand-duplicated Jinja copies."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("findings", "severity_points"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        findings = self._task.args.get("findings")
        severity_points = self._task.args.get("severity_points")
        if findings is None or severity_points is None:
            result["failed"] = True
            result["msg"] = "compute_review_scores requires 'findings' and 'severity_points' arguments"
            return result

        try:
            computed = compute_scores_and_verdict(findings, severity_points)
        except InvalidFindingError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result

        result["changed"] = False
        result.update(computed)
        return result
