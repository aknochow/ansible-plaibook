# -*- coding: utf-8 -*-
"""Shared action plugin: build findings.md's YAML frontmatter from real facts.

Builds frontmatter directly from Ansible facts (verdict, scores) rather
than regex-parsing the already-rendered report text for a "### Verdict:"
line and a scores table. Consuming the facts merge.yml/
compute_review_scores.py already computed avoids a fragile re-derivation
step and lets every field be unconditionally present, rather than
silently omitted when a regex fails to match an unusually-shaped report.

project/branch values are sanitized (quotes and newlines stripped)
before being embedded in a quoted YAML string, since an unescaped
newline could otherwise break out of the value and inject an arbitrary
frontmatter key. Score fields are coerced to float and formatted with
`:.1f` for consistent, bounded-length output regardless of caller.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase


def build_review_frontmatter(
    today_date: str,
    commit: str,
    project: str,
    branch: str,
    verdict: str,
    score_overall: float,
    score_functionality: float,
    score_security: float,
    score_quality: float,
) -> str:
    def _sanitize(value: str) -> str:
        return value.replace(chr(34), "").replace("\n", "").replace("\r", "")

    lines = ["---"]
    lines.append(f"date: {today_date}")
    lines.append(f"commit: {commit}")
    lines.append(f'project: "{_sanitize(project)}"')
    lines.append(f'branch: "{_sanitize(branch)}"')
    lines.append(f"verdict: {verdict}")
    lines.append(f"score: {float(score_overall):.1f}")
    lines.append("scores:")
    lines.append(f"  functionality: {float(score_functionality):.1f}")
    lines.append(f"  security: {float(score_security):.1f}")
    lines.append(f"  quality: {float(score_quality):.1f}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


class ActionModule(ActionBase):
    """Build findings.md's YAML frontmatter from real facts -- real Python instead of a write-then-regex-then-rewrite pass."""

    _requires_connection = False
    _VALID_ARGS = frozenset((
        "today_date", "commit", "project", "branch", "verdict",
        "score_overall", "score_functionality", "score_security", "score_quality",
    ))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        kwargs = {arg: self._task.args.get(arg) for arg in self._VALID_ARGS}
        missing = [arg for arg, value in kwargs.items() if value is None]
        if missing:
            result["failed"] = True
            result["msg"] = f"build_review_frontmatter requires {sorted(self._VALID_ARGS)} arguments; missing: {sorted(missing)}"
            return result

        result["changed"] = False
        result["frontmatter"] = build_review_frontmatter(**kwargs)
        return result
