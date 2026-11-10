# -*- coding: utf-8 -*-
"""Shared action plugin: handle findings whose replacement changes nothing.

A finding can carry a ``replacement`` that is identical to the lines it
cites. Such a finding refutes itself: its own fix leaves the code as it is
(a reported "missing annotations" whose replacement is the same annotated
line, for example).

The cited lines come from the new side of the review diff, so no checkout
is needed. When a cited line is not in the diff, the finding is left
alone: there is nothing to compare against.

- Nit and Minor: dropped. Nothing else re-checks these severities.
- Critical and Major: kept with ``replacement`` cleared. The verify pass
  still decides whether the finding is real.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_DROP_SEVERITIES = frozenset({"Nit", "Minor"})


def _unquote(quoted: str) -> str:
    """Decode git's C-style path quoting: \\", \\\\, \\t, and octal UTF-8 bytes."""
    try:
        return quoted.encode("ascii").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except (UnicodeError, ValueError):
        return quoted


def new_side_lines(diff: str) -> dict[str, dict[int, str]]:
    """Map each file in a unified diff to its new-side line numbers and text."""
    files: dict[str, dict[int, str]] = {}
    current: dict[int, str] | None = None
    in_hunk = False
    line_no = 0
    for raw in (diff or "").splitlines():
        if raw.startswith("diff --git "):
            current, in_hunk = None, False
            continue
        match = _HUNK.match(raw)
        if match:
            line_no, in_hunk = int(match.group(1)), True
            continue
        if not in_hunk:
            # File headers. Inside a hunk, "+++ x" is an added line "++ x".
            if raw.startswith("+++ "):
                # git ends a path containing a space with a tab, and quotes
                # a path with a quote, a backslash, or a control character.
                path = raw[4:].rstrip("\t")
                if len(path) > 1 and path.startswith('"') and path.endswith('"'):
                    path = _unquote(path[1:-1])
                if path.startswith("b/"):
                    path = path[2:]
                current = None if path == "/dev/null" else files.setdefault(path, {})
            continue
        if current is None:
            continue
        # An empty line is a context line whose single space was stripped.
        marker, text = (raw[0], raw[1:]) if raw else (" ", "")
        if marker in ("+", " "):
            current[line_no] = text
            line_no += 1
        # "-" lines and "\\ No newline at end of file" do not advance the new side.
    return files


def cited_lines(finding: dict, files: dict[str, dict[int, str]]) -> list[str] | None:
    """The new-side lines a finding cites, or None when any is not in the diff."""
    lines = files.get(str(finding.get("file") or ""))
    if not lines:
        return None
    try:
        end = int(finding.get("line"))
        raw_start = finding.get("start_line")
        start = int(raw_start) if raw_start not in (None, "") else end
    except (TypeError, ValueError):
        return None
    if not 1 <= start <= end:
        return None
    if any(number not in lines for number in range(start, end + 1)):
        return None
    return [lines[number] for number in range(start, end + 1)]


def is_noop_replacement(finding: dict, files: dict[str, dict[int, str]]) -> bool:
    replacement = finding.get("replacement")
    if not isinstance(replacement, str) or not replacement.strip():
        return False
    current = cited_lines(finding, files)
    if current is None:
        return False
    # Exact text: a replacement that only removes trailing whitespace is a
    # real change (a whitespace Nit, a Markdown hard break).
    return replacement.strip("\n").splitlines() == current


def drop_noop_replacements(findings: list, diff: str) -> tuple[list, list, int]:
    """Return (kept findings, dropped findings, count of replacements cleared)."""
    if not isinstance(findings, list):
        raise TypeError("findings must be a list")
    files = new_side_lines(diff)
    kept: list = []
    dropped: list = []
    cleared = 0
    for finding in findings:
        if not isinstance(finding, dict) or not is_noop_replacement(finding, files):
            kept.append(finding)
            continue
        if finding.get("severity") in _DROP_SEVERITIES:
            dropped.append(finding)
            continue
        cleaned = dict(finding)
        cleaned["replacement"] = ""
        kept.append(cleaned)
        cleared += 1
    return kept, dropped, cleared


class ActionModule(ActionBase):
    """Drop Nit/Minor findings whose replacement is the cited code; clear it on others."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("findings", "full_diff"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = {}
        result = super().run(tmp, task_vars)
        del tmp
        findings = self._task.args.get("findings")
        if findings is None:
            result["failed"] = True
            result["msg"] = "drop_noop_replacements requires a 'findings' argument"
            return result
        try:
            kept, dropped, cleared = drop_noop_replacements(findings, self._task.args.get("full_diff") or "")
        except TypeError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result
        result["changed"] = False
        result["findings"] = kept
        result["dropped"] = [f"{item.get('file')}:{item.get('line')} ({item.get('severity')})" for item in dropped]
        result["dropped_count"] = len(dropped)
        result["cleared_count"] = cleared
        return result
