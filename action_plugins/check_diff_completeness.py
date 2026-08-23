# -*- coding: utf-8 -*-
"""Shared action plugin: completeness signals (deleted files, TODO/FIXME, trailing whitespace).

These signals are rendered directly into findings.md's human-facing
report: genuinely reviewer-relevant ("did you mean to delete this,"
"you shipped N new TODOs"). The deleted-files git command runs as a
plain Ansible task, not wrapped in this plugin; this plugin only does
the pure regex analysis over already-fetched diff content and the
already-fetched deleted-files list.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

_TODO_FIXME_PATTERN = re.compile(r"TODO|FIXME|HACK|XXX")
_TRAILING_WHITESPACE_PATTERN = re.compile(r"\s+$")
_MAX_TODO_FIXME_LINES = 10


def _is_added_line(line: str) -> bool:
    return line.startswith("+") and not line.startswith("+++")


def check_diff_completeness(diff_content: str, deleted_files: list[str]) -> dict:
    added_lines = [line for line in diff_content.splitlines() if _is_added_line(line)]
    todo_fixme = [line for line in added_lines if _TODO_FIXME_PATTERN.search(line)]
    trailing_whitespace = sum(1 for line in added_lines if _TRAILING_WHITESPACE_PATTERN.search(line))

    return {
        "deleted_files": deleted_files,
        "todo_fixme": todo_fixme[:_MAX_TODO_FIXME_LINES],
        "trailing_whitespace": trailing_whitespace,
    }


class ActionModule(ActionBase):
    """Completeness signals from a diff -- real Python instead of a hand-rolled Jinja/regex chain."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("diff_content", "deleted_files"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        diff_content = self._task.args.get("diff_content")
        deleted_files = self._task.args.get("deleted_files")
        if diff_content is None or deleted_files is None:
            result["failed"] = True
            result["msg"] = "check_diff_completeness requires 'diff_content' and 'deleted_files' arguments"
            return result

        result["changed"] = False
        result.update(check_diff_completeness(diff_content, deleted_files))
        return result
