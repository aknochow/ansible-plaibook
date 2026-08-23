# -*- coding: utf-8 -*-
"""Shared action plugin: categorize changed files by type for structured metrics.

Category order matters: a file matching an earlier rule (docs, tests,
config) never falls through to the stats-based rules (deleted/
new_logic/modified_logic) even if its stats would also match one of
those, e.g. a deleted .md file lands in "docs", not "deleted".
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase

_DOC_SUFFIXES = (".md", ".rst", ".txt")
_CONFIG_SUFFIXES = (".yml", ".yaml", ".toml", ".cfg", ".json", ".env")
_CONFIG_EXACT_NAMES = (".gitignore", ".gitleaksignore", ".pre-commit-config.yaml")


def categorize_diff_files(files: list[str], stats: dict[str, dict[str, int]]) -> dict[str, list[str]]:
    categories = {
        "new_logic": [],
        "modified_logic": [],
        "config": [],
        "docs": [],
        "tests": [],
        "deleted": [],
    }
    for f in files:
        s = stats.get(f, {})
        if f.endswith(_DOC_SUFFIXES) and not f.startswith("test"):
            categories["docs"].append(f)
        elif f.startswith("test") or "/test" in f or f.endswith("_test.go"):
            categories["tests"].append(f)
        elif f.endswith(_CONFIG_SUFFIXES) or f in _CONFIG_EXACT_NAMES:
            categories["config"].append(f)
        elif s.get("deletions", 0) > 0 and s.get("additions", 0) == 0:
            categories["deleted"].append(f)
        elif s.get("additions", 0) > 0 and s.get("deletions", 0) == 0:
            categories["new_logic"].append(f)
        else:
            categories["modified_logic"].append(f)
    return categories


class ActionModule(ActionBase):
    """Categorize changed files by type -- real Python instead of a hand-rolled Jinja loop."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("files", "stats"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        files = self._task.args.get("files")
        stats = self._task.args.get("stats")
        if files is None or stats is None:
            result["failed"] = True
            result["msg"] = "categorize_diff_files requires 'files' and 'stats' arguments"
            return result

        result["changed"] = False
        result["categories"] = categorize_diff_files(files, stats)
        return result
