# -*- coding: utf-8 -*-
"""Shared action plugin: two-pass findings dedup.

Third port for the action-plugin migration roadmap
(handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml, item
port-merge-yml-dedup) -- the most convoluted, most historically
bug-prone Jinja in merge.yml: two dedup passes (exact file:line, then
file + whitespace-collapsed evidence text), each needing groupby +
map('first'), each requiring a re-sort immediately before it because
Jinja's groupby filter internally re-sorts by ITS OWN grouping key
(sorted(value, key=expr) before itertools.groupby -- confirmed by
reading jinja2.filters.sync_do_groupby's actual source), which
scrambles the severity-descending order the previous pass's output
needs going in. A single dict tracking best-severity-so-far per key
needs none of that -- no sort, no re-sort, no groupby.

Two discovered behaviors preserved/changed deliberately, not by
accident (see test_dedupe_findings.py for the equivalence proof of
each):

1. PRESERVED: Jinja's groupby is case-INSENSITIVE by default
   (jinja2.filters.ignore_case lowercases string keys before grouping,
   confirmed by reading its source -- not documented behavior most
   readers would guess). dedup_key/evidence_dedup_key are both plain
   f-strings, so today's real merge.yml genuinely dedupes
   "app.py:42" and "APP.py:42" as the same finding. Replicated exactly
   here (.lower() on both keys) for true equivalence -- diverging
   "because case-sensitivity seems more correct" would be an
   undisclosed behavior change smuggled into a port, exactly what this
   migration's equivalence-testing discipline exists to prevent.

2. CHANGED (disclosed): output order. The legacy Jinja's final order is
   an INCIDENTAL side effect of the second groupby's internal re-sort
   (alphabetical-ish by evidence_dedup_key), not a meaningful contract
   -- confirmed nothing downstream reads dedup_key/evidence_dedup_key/
   severity_rank (verify.yml's own comment confirms explore-pass
   findings never even have these fields). This implementation instead
   preserves first-occurrence order from the input findings list, which
   is simpler to reason about and test. If finding.md.j2's rendering
   order ever needs to match the old behavior exactly, that's a
   template-level sort to add there, not a reason to reintroduce
   groupby's incidental ordering here.

Also drops dedup_key/evidence_dedup_key/severity_rank from the output
entirely -- these were merge.yml's own internal bookkeeping fields for
the Jinja implementation's multi-pass dance, never part of the findings
schema, never read downstream. The plugin's own internal computation
doesn't need to leak into the returned finding dicts.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

_REQUIRED_KEYS = ("file", "line", "severity", "evidence")


class InvalidFindingError(ValueError):
    """Raised when a finding is missing a required key, or has a severity not in severity_points.

    Same rationale as compute_review_scores.py's InvalidFindingError
    (findings are LLM-authored and schema-constrained, but this codebase
    doesn't fully trust model compliance elsewhere either) -- fail
    loudly with a clear message here rather than a raw KeyError deep
    inside a lambda passed to _dedupe_by_key.
    """


def _validate_findings(findings: list[dict], severity_points: dict[str, float]) -> None:
    for required_key in _REQUIRED_KEYS:
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


def _collapse_whitespace(text: str) -> str:
    """Collapse all whitespace runs to a single space, then strip ends.

    Matches merge.yml's `| regex_replace('\\s+', ' ') | trim` exactly --
    a naive `| trim` alone only strips the ends, not internal whitespace,
    which is the real bug this collapsing fixes (two lens prompts quoting
    the same source line with different internal indentation, e.g. one
    tab vs. three tabs before a token, must still be recognized as the
    same evidence).
    """
    return re.sub(r"\s+", " ", text).strip()


def _dedupe_by_key(findings: list[dict], key_fn, severity_points: dict[str, float]) -> list[dict]:
    """Keep the highest-severity survivor per key_fn(finding), case-insensitively.

    First-occurrence order preserved for the surviving key's position
    (see module docstring point 2) -- a later-occurring, higher-severity
    finding for the same key still replaces the earlier one's CONTENT,
    but occupies the earlier one's POSITION in the output.
    """
    best_by_key: dict[str, dict] = {}
    order: list[str] = []
    for finding in findings:
        key = key_fn(finding).lower()
        if key not in best_by_key:
            order.append(key)
            best_by_key[key] = finding
        elif severity_points[finding["severity"]] > severity_points[best_by_key[key]["severity"]]:
            best_by_key[key] = finding
    return [best_by_key[key] for key in order]


def dedupe_findings(findings: list[dict], severity_points: dict[str, float]) -> list[dict]:
    """Two-pass dedup: exact file:line first, then file + collapsed evidence text."""
    _validate_findings(findings, severity_points)

    line_deduped = _dedupe_by_key(
        findings,
        lambda finding: f"{finding['file']}:{finding['line']}",
        severity_points,
    )
    evidence_deduped = _dedupe_by_key(
        line_deduped,
        lambda finding: f"{finding['file']}|{_collapse_whitespace(finding['evidence'])}",
        severity_points,
    )
    return evidence_deduped


class ActionModule(ActionBase):
    """Two-pass findings dedup -- real Python instead of a groupby/re-sort dance."""

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
            result["msg"] = "dedupe_findings requires 'findings' and 'severity_points' arguments"
            return result

        try:
            deduped = dedupe_findings(findings, severity_points)
        except InvalidFindingError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result

        result["changed"] = False
        result["findings"] = deduped
        return result
