# -*- coding: utf-8 -*-
"""Drop generated lockfile hunks from a copy of the lens diff.

review_full_diff stays the real git diff. The lens prompt receives a stub
for each denylisted file: path, additions, and deletions, with no hash lines.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from ansible.plugins.action import ActionBase

_EXACT_NAMES = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "go.sum",
        "Gemfile.lock",
        "composer.lock",
        "packages.lock.json",
    }
)


def is_generated_lockfile(path: str) -> bool:
    """True when either diff path is a generated lockfile."""
    name = PurePosixPath(path).name
    if name in _EXACT_NAMES:
        return True
    parts = PurePosixPath(path).parts
    return (
        len(parts) >= 3
        and parts[0] == "plaibook"
        and parts[1] == "hashed"
        and name.endswith("-requirements.txt")
    )


def _paths(header: str) -> tuple[str, str]:
    prefix = "diff --git a/"
    if not header.startswith(prefix) or " b/" not in header[len(prefix) :]:
        return "", ""
    left, right = header[len(prefix) :].rsplit(" b/", 1)
    return left, right


def _line_counts(body: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in body.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def _split_files(diff_content: str) -> list[tuple[str, str]]:
    """Return (header, remainder) for each file section, header included in neither twice."""
    if not diff_content:
        return []
    lines = diff_content.splitlines(keepends=True)
    sections: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    parsed: list[tuple[str, str]] = []
    for section in sections:
        header = section[0].rstrip("\n")
        body = "".join(section[1:])
        parsed.append((header, body))
    return parsed


def omit_generated_diff_files(diff_content: str) -> dict:
    """Return a lens diff with generated lockfile hunks replaced by stubs."""
    if not diff_content:
        return {"lens_diff": "", "omitted": [], "omitted_line_count": 0}

    pieces: list[str] = []
    omitted: list[dict] = []
    omitted_line_count = 0
    for header, body in _split_files(diff_content):
        section = header + ("\n" if not header.endswith("\n") else "") + body
        if not header.startswith("diff --git "):
            pieces.append(section)
            continue
        left, right = _paths(header)
        if not (is_generated_lockfile(left) or is_generated_lockfile(right)):
            pieces.append(section)
            continue
        additions, deletions = _line_counts(body)
        path = right or left
        omitted.append(
            {"path": path, "additions": additions, "deletions": deletions}
        )
        omitted_line_count += section.count("\n") + (0 if section.endswith("\n") else 1)
        stub = (
            f"{header}\n"
            f"# omitted generated file: {path}\n"
            f"# additions: {additions}\n"
            f"# deletions: {deletions}\n"
        )
        pieces.append(stub)
    lens = "".join(pieces)
    return {
        "lens_diff": lens,
        "omitted": omitted,
        "omitted_line_count": omitted_line_count,
    }


class ActionModule(ActionBase):
    """Replace generated lockfile hunks in a copy of the lens diff."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("diff_content",))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        diff_content = self._task.args.get("diff_content")
        if diff_content is None:
            result["failed"] = True
            result["msg"] = "omit_generated_diff_files requires a 'diff_content' argument"
            return result

        payload = omit_generated_diff_files(diff_content)
        result["changed"] = False
        result.update(payload)
        return result
