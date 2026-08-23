# -*- coding: utf-8 -*-
"""Shared action plugin: pick the latest prior review directory with a findings.md.

Prior-review directories follow a fixed
`<base_dir>/<org/repo>/<branch>/<date>-<sha>/findings.md` naming
convention (see persist.yml's own write path). This plugin takes an
already-listed set of candidate directory names (a plain Ansible
`find` task handles the controller-side directory listing) and picks
the most recent one matching that convention with a real findings.md
present.

`review_same_commit_fast_path_enabled` (roles/review/defaults/main.yml)
gates whether a same-commit match short-circuits the rest of the
pipeline, with an explicit disable flag for cases where a fresh
dispatch is wanted regardless.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

_REVIEW_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{7,}$")


def find_latest_prior_review(dirs_with_findings: list[str]) -> str | None:
    entries = sorted(
        (name for name in dirs_with_findings if _REVIEW_DIR_PATTERN.match(name)),
        reverse=True,
    )
    return entries[0] if entries else None


class ActionModule(ActionBase):
    """Pick the latest prior-review directory."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("dirs_with_findings",))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        dirs_with_findings = self._task.args.get("dirs_with_findings")
        if dirs_with_findings is None:
            result["failed"] = True
            result["msg"] = "find_latest_prior_review requires a 'dirs_with_findings' argument"
            return result

        latest = find_latest_prior_review(dirs_with_findings)
        result["changed"] = False
        result["found"] = latest is not None
        result["latest_dir"] = latest or ""
        return result
