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


def _read_quoted(text: str, start: int) -> tuple[str, int]:
    """Return the C-quoted token at start, including its quotes, and the next index."""
    if start >= len(text) or text[start] != '"':
        raise ValueError("expected a quoted diff token")
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            if index + 1 >= len(text):
                raise ValueError("trailing backslash in a quoted diff token")
            index += 2
            continue
        if text[index] == '"':
            return text[start : index + 1], index + 1
        index += 1
    raise ValueError("unterminated quoted diff token")


def _unquote_c_style(token: str) -> str:
    """Decode one git C-quoted path token back to a Unicode path."""
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        raise ValueError("diff token is not C-quoted")
    simple = {
        "a": 0x07,
        "b": 0x08,
        "t": 0x09,
        "n": 0x0A,
        "v": 0x0B,
        "f": 0x0C,
        "r": 0x0D,
        "\\": 0x5C,
        '"': 0x22,
    }
    raw = bytearray()
    inner = token[1:-1]
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "\\":
            raw.extend(char.encode("utf-8"))
            index += 1
            continue
        if index + 1 >= len(inner):
            raise ValueError("trailing backslash in a quoted diff token")
        escaped = inner[index + 1]
        if escaped in simple:
            raw.append(simple[escaped])
            index += 2
            continue
        if escaped not in "01234567":
            raise ValueError("unknown git path escape")
        octal = ""
        cursor = index + 1
        while cursor < len(inner) and len(octal) < 3 and inner[cursor] in "01234567":
            octal += inner[cursor]
            cursor += 1
        raw.append(int(octal, 8))
        index = cursor
    return raw.decode("utf-8")


def _strip_side(path: str, prefix: str) -> str:
    if not path.startswith(prefix):
        raise ValueError("quoted diff path is missing its a/ or b/ prefix")
    return path[len(prefix) :]


def _quoted_paths(rest: str) -> tuple[str, str]:
    left_token, index = _read_quoted(rest, 0)
    if index >= len(rest) or rest[index] != " " or index + 1 >= len(rest):
        raise ValueError("quoted diff header is missing its second path")
    right_token, end = _read_quoted(rest, index + 1)
    if end != len(rest):
        raise ValueError("quoted diff header has trailing text")
    return (
        _strip_side(_unquote_c_style(left_token), "a/"),
        _strip_side(_unquote_c_style(right_token), "b/"),
    )


def _paths(header: str) -> tuple[str, str]:
    """Return repo paths from a diff --git header.

    Raises ValueError when the line is a diff header this parser cannot read.
    Returns empty strings for a line that is not a diff header.
    """
    header = header.rstrip("\r")
    marker = "diff --git "
    if not header.startswith(marker):
        return "", ""
    rest = header[len(marker) :]
    if rest.startswith('"'):
        return _quoted_paths(rest)
    prefix = "a/"
    if not rest.startswith(prefix) or " b/" not in rest[len(prefix) :]:
        raise ValueError("unquoted diff header has no a/ and b/ paths")
    left, right = rest[len(prefix) :].rsplit(" b/", 1)
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
        return {
            "lens_diff": "",
            "omitted": [],
            "omitted_line_count": 0,
            "unparsed_headers": [],
        }

    pieces: list[str] = []
    omitted: list[dict] = []
    unparsed_headers: list[str] = []
    omitted_line_count = 0
    for header, body in _split_files(diff_content):
        section = header + ("\n" if not header.endswith("\n") else "") + body
        if not header.startswith("diff --git "):
            pieces.append(section)
            continue
        try:
            left, right = _paths(header)
        except ValueError:
            unparsed_headers.append(header)
            pieces.append(section)
            continue
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
        "unparsed_headers": unparsed_headers,
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
        for header in payload.get("unparsed_headers") or []:
            self._display.warning(
                "omit_generated_diff_files left a diff section unchanged "
                "because its header could not be parsed: " + header
            )
        return result
