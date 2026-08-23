# -*- coding: utf-8 -*-
"""Shared action plugin: find remaining hardcoded org references after a neutralization pass.

Conditionally triggered: only scans when the diff itself mentions
neutralization-related keywords ("org-neutral," "hardcoded,"
"config-driven," etc.), on the theory that a diff claiming to remove
hardcoded references is exactly the diff worth double-checking for
ones it missed.

`org_reference_patterns` has no shipped default (see review.yml's
`review_neutralization_org_patterns`): this check is only meaningful
once an operator supplies their own org's actual internal strings to
watch for, so an empty list (check silently declines to run) is the
only honest out-of-the-box default for a tool with no fixed home org.

Reuses `briefing.yml`'s already-fetched diff/file-contents facts rather
than re-reading each changed file's content a second time. File
contents are truncated at 1MB and omit deleted/oversized/non-regular
files, which has no practical effect for the source code this check
cares about.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase

_NEUTRALIZATION_SIGNALS = (
    "org-neutral", "neutraliz", "hardcoded", "config-driven",
    "org-specific", "org-agnostic",
)
_EXCLUDED_EXTENSIONS = (".md", ".txt", ".rst")
_MAX_LINE_LENGTH = 100


def check_neutralization_references(
    diff_content: str, changed_files: list[str], file_contents: dict, org_reference_patterns: list[str] = ()
) -> list[str] | None:
    diff_lower = diff_content.lower()
    if not any(signal in diff_lower for signal in _NEUTRALIZATION_SIGNALS):
        return None
    if not org_reference_patterns:
        return None

    lowered_patterns = [p.lower() for p in org_reference_patterns]
    results = []
    for filepath in changed_files:
        if filepath.endswith(_EXCLUDED_EXTENSIONS):
            continue
        content = file_contents.get(filepath)
        if content is None:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            lower = line.lower()
            if any(pattern in lower for pattern in lowered_patterns):
                results.append(f"{filepath}:{i}: {stripped[:_MAX_LINE_LENGTH]}")

    return results if results else None


class ActionModule(ActionBase):
    """Remaining-hardcoded-reference scan -- real Python instead of a hand-rolled Jinja/regex chain."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("diff_content", "changed_files", "file_contents", "org_reference_patterns"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        diff_content = self._task.args.get("diff_content")
        changed_files = self._task.args.get("changed_files")
        file_contents = self._task.args.get("file_contents")
        org_reference_patterns = self._task.args.get("org_reference_patterns") or []
        if diff_content is None or changed_files is None or file_contents is None:
            result["failed"] = True
            result["msg"] = (
                "check_neutralization_references requires 'diff_content', "
                "'changed_files', and 'file_contents' arguments"
            )
            return result

        refs = check_neutralization_references(diff_content, changed_files, file_contents, org_reference_patterns)
        result["changed"] = False
        result["triggered"] = refs is not None
        result["remaining_refs"] = refs or []
        return result
