# -*- coding: utf-8 -*-
"""Blank comments out of a unified diff in place. Never delete lines.

Lens-phase consumer of a *copy* of the prepared diff. The original
``review_full_diff`` is left untouched so verify, findings.md, and
summary.json line citations still map to the real files. A line-number
shift here would make every finding cite the wrong line in the report
a human reads, and would make ``post_review_comment.py`` post inline
PR comments on the wrong lines.

The plugin itself asserts equal per-file line counts (and equal total
line count) before returning. Callers must still assert in the
playbook: a unit test is not a substitute for the pipeline invariant.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase

_DIFF_FILE_PREFIX = "diff --git "


def _split_keepends(text: str) -> list[str]:
    if text == "":
        return []
    return text.splitlines(keepends=True)


def _line_parts(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _is_diff_file_header(line: str) -> bool:
    body, _ = _line_parts(line)
    return body.startswith(_DIFF_FILE_PREFIX)


def _is_hunk_header(line: str) -> bool:
    body, _ = _line_parts(line)
    return body.startswith("@@")


def _is_new_old_file_header(line: str) -> bool:
    body, _ = _line_parts(line)
    return body.startswith("+++ ") or body.startswith("--- ")


def _is_hunk_body_line(line: str) -> bool:
    """True for added/removed/context lines, not +++ / --- file headers."""
    if _is_new_old_file_header(line):
        return False
    body, _ = _line_parts(line)
    return body.startswith(("+", "-", " "))


def _unquote_git_path(path: str) -> str:
    path = path.strip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        return path[1:-1]
    return path


def _path_from_plus_plus_line(line: str) -> str | None:
    """New-file path from ``+++ b/path``. Prefer this over ``diff --git``."""
    body, _ = _line_parts(line)
    if not body.startswith("+++ "):
        return None
    rest = body[4:]
    if rest == "/dev/null":
        return ""
    if rest.startswith("b/"):
        return _unquote_git_path(rest[2:])
    return _unquote_git_path(rest)


def _path_from_diff_header(header_body: str) -> str:
    """``diff --git a/<path> b/<path>``. Do not rfind `` b/`` (paths may contain it)."""
    rest = header_body[len(_DIFF_FILE_PREFIX) :]
    if rest.startswith("a/") and " b/" in rest:
        return _unquote_git_path(rest.split(" b/", 1)[1])
    return _unquote_git_path(rest.split(" ", 1)[-1])


def language_for_path(path: str) -> str:
    lower = path.lower()
    jinja = lower.endswith((".j2", ".jinja", ".jinja2"))
    base = lower
    if jinja:
        for suffix in (".j2", ".jinja2", ".jinja"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
    if base.endswith((".py", ".pyi")):
        lang = "python"
    elif base.endswith((".yml", ".yaml")):
        lang = "yaml"
    elif base.endswith((".md", ".markdown")):
        lang = "markdown"
    elif jinja:
        lang = "jinja"
    else:
        lang = "unknown"
    if jinja and lang != "jinja":
        return f"{lang}+jinja"
    return lang


class _ScreenState:
    __slots__ = (
        "py_triple",
        "py_triple_is_docstring",
        "jinja_comment",
        "html_comment",
        "yaml_single",
        "yaml_double",
    )

    def __init__(self) -> None:
        self.py_triple: str | None = None
        self.py_triple_is_docstring = False
        self.jinja_comment = False
        self.html_comment = False
        self.yaml_single = False
        self.yaml_double = False


def _python_docstring_prefix_ok(prefix: str) -> bool:
    # A docstring is a triple-quoted string that is the first statement on
    # the line (optional whitespace only). Assignments and call args are not.
    return prefix.strip() == ""


def _screen_python_line(content: str, state: _ScreenState, screen_docstrings: bool) -> str:
    out: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        if state.py_triple:
            closer = state.py_triple
            if content.startswith(closer, i):
                if screen_docstrings and state.py_triple_is_docstring:
                    out.append(" " * len(closer))
                else:
                    out.append(closer)
                i += len(closer)
                state.py_triple = None
                state.py_triple_is_docstring = False
                continue
            if screen_docstrings and state.py_triple_is_docstring:
                out.append(" " if content[i] != "\t" else "\t")
            else:
                out.append(content[i])
            i += 1
            continue

        if content.startswith(('"""', "'''"), i):
            quote = content[i : i + 3]
            prefix = "".join(out)
            is_doc = _python_docstring_prefix_ok(prefix)
            state.py_triple = quote
            state.py_triple_is_docstring = is_doc
            if screen_docstrings and is_doc:
                out.append(" " * 3)
            else:
                out.append(quote)
            i += 3
            continue

        ch = content[i]
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                c = content[i]
                out.append(c)
                i += 1
                if c == "\\" and i < n:
                    out.append(content[i])
                    i += 1
                    continue
                if c == quote:
                    break
            continue

        if ch == "#":
            rest = content[i:]
            out.append(" " * len(rest))
            break

        out.append(ch)
        i += 1
    return "".join(out)


def _screen_yaml_line(content: str, state: _ScreenState) -> str:
    # YAML comments: `#` at column 0 or preceded by whitespace, outside quotes.
    out: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        ch = content[i]
        if state.yaml_single:
            out.append(ch)
            i += 1
            if ch == "'":
                if i < n and content[i] == "'":
                    out.append("'")
                    i += 1
                else:
                    state.yaml_single = False
            continue
        if state.yaml_double:
            out.append(ch)
            i += 1
            if ch == "\\" and i < n:
                out.append(content[i])
                i += 1
                continue
            if ch == '"':
                state.yaml_double = False
            continue
        if ch == "'":
            state.yaml_single = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            state.yaml_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "#" and (i == 0 or content[i - 1].isspace()):
            out.append(" " * (n - i))
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _screen_jinja_line(content: str, state: _ScreenState) -> str:
    out: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        if state.jinja_comment:
            if content.startswith("#}", i):
                out.append("  ")
                i += 2
                state.jinja_comment = False
                continue
            out.append(" " if content[i] != "\t" else "\t")
            i += 1
            continue
        if content.startswith("{#", i):
            out.append("  ")
            i += 2
            state.jinja_comment = True
            continue
        out.append(content[i])
        i += 1
    return "".join(out)


def _screen_markdown_line(content: str, state: _ScreenState) -> str:
    out: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        if state.html_comment:
            if content.startswith("-->", i):
                out.append("   ")
                i += 3
                state.html_comment = False
                continue
            out.append(" " if content[i] != "\t" else "\t")
            i += 1
            continue
        if content.startswith("<!--", i):
            out.append("    ")
            i += 4
            state.html_comment = True
            continue
        out.append(content[i])
        i += 1
    return "".join(out)


_SCREEN_LANG_ORDER = ("jinja", "markdown", "python", "yaml")


def screen_content_line(content: str, languages: list[str], state: _ScreenState, screen_docstrings: bool) -> str:
    """Mask Jinja/HTML comments before base-language lexers see their contents."""
    text = content
    active = set(languages)
    for lang in _SCREEN_LANG_ORDER:
        if lang not in active:
            continue
        if lang == "python":
            text = _screen_python_line(text, state, screen_docstrings)
        elif lang == "yaml":
            text = _screen_yaml_line(text, state)
        elif lang == "jinja":
            text = _screen_jinja_line(text, state)
        elif lang == "markdown":
            text = _screen_markdown_line(text, state)
    return text


def _languages_for(lang: str) -> list[str]:
    if "+" in lang:
        return [part for part in lang.split("+") if part != "unknown"]
    if lang == "unknown":
        return []
    return [lang]


def screen_hunk_body_line(line: str, languages: list[str], new_state: _ScreenState, old_state: _ScreenState, screen_docstrings: bool) -> str:
    body, ending = _line_parts(line)
    prefix = body[0]
    content = body[1:]
    if prefix == "-":
        screened = screen_content_line(content, languages, old_state, screen_docstrings)
    else:
        # '+' added and ' ' context follow the new-file stream.
        screened = screen_content_line(content, languages, new_state, screen_docstrings)
        if prefix == " ":
            # Keep old-file tokenizer in step on context lines.
            screen_content_line(content, languages, old_state, screen_docstrings)
    return prefix + screened + ending


def screen_hunk_header_line(line: str, languages: list[str], screen_docstrings: bool) -> str:
    """Keep ``@@ -l,s +l,s @@`` ranges; blank comment text in the source suffix."""
    body, ending = _line_parts(line)
    first = body.find("@@")
    second = body.find("@@", first + 2) if first >= 0 else -1
    if first < 0 or second < 0:
        return line
    ranges = body[: second + 2]
    suffix = body[second + 2 :]
    if not suffix:
        return line
    lead = ""
    rest = suffix
    if rest.startswith(" "):
        lead = " "
        rest = rest[1:]
    if not rest or not languages:
        return line
    screened = screen_content_line(rest, languages, _ScreenState(), screen_docstrings)
    return ranges + lead + screened + ending


def _iter_file_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    if not lines:
        return []
    sections: list[tuple[str, list[str]]] = []
    current_path = ""
    current: list[str] = []
    preamble: list[str] = []
    for line in lines:
        if _is_diff_file_header(line):
            if current:
                sections.append((current_path, current))
            elif preamble:
                sections.append(("", preamble))
                preamble = []
            body, _ = _line_parts(line)
            current_path = _path_from_diff_header(body)
            current = [line]
        elif current:
            plus_path = _path_from_plus_plus_line(line)
            if plus_path:
                current_path = plus_path
            current.append(line)
        else:
            preamble.append(line)
    if current:
        sections.append((current_path, current))
    elif preamble:
        sections.append(("", preamble))
    return sections


def screen_unified_diff(diff_content: str, *, screen_docstrings: bool = False) -> dict:
    """Return screened_diff plus per-file line-count comparison.

    Never deletes lines. On a count mismatch the screened text is still
    returned so the caller can fail with context, but ``line_counts_match``
    is False and the action plugin treats that as a failure.
    """
    original_lines = _split_keepends(diff_content)
    screened_lines: list[str] = []
    per_file: list[dict] = []

    for path, section in _iter_file_sections(original_lines):
        languages = _languages_for(language_for_path(path)) if path else []
        new_state = _ScreenState()
        old_state = _ScreenState()
        out_section: list[str] = []
        for line in section:
            if languages and _is_hunk_header(line):
                # Omitted lines between hunks are not in the diff: do not
                # carry lexer state across them. The suffix after the second
                # @@ is source context (often a function line + inline comment).
                new_state = _ScreenState()
                old_state = _ScreenState()
                out_section.append(screen_hunk_header_line(line, languages, screen_docstrings))
            elif languages and _is_hunk_body_line(line):
                out_section.append(
                    screen_hunk_body_line(line, languages, new_state, old_state, screen_docstrings)
                )
            else:
                # Diff metadata, binary notices, unknown langs.
                out_section.append(line)
        per_file.append(
            {
                "path": path or "(preamble)",
                "original_lines": len(section),
                "screened_lines": len(out_section),
            }
        )
        screened_lines.extend(out_section)

    screened = "".join(screened_lines)
    # Preserve a missing trailing newline: splitlines(keepends=True) already
    # does. If the original was empty, stay empty.
    line_counts_match = len(screened_lines) == len(original_lines) and all(
        item["original_lines"] == item["screened_lines"] for item in per_file
    )
    return {
        "screened_diff": screened,
        "line_counts_match": line_counts_match,
        "original_line_count": len(original_lines),
        "screened_line_count": len(screened_lines),
        "per_file": per_file,
    }


class ActionModule(ActionBase):
    """Blank comments in a copy of the prepared diff. Never mutates the original."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("diff_content", "screen_docstrings"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        diff_content = self._task.args.get("diff_content")
        if diff_content is None:
            result["failed"] = True
            result["msg"] = "screen_diff_comments requires a 'diff_content' argument"
            return result

        screen_docstrings = bool(self._task.args.get("screen_docstrings", False))
        payload = screen_unified_diff(diff_content, screen_docstrings=screen_docstrings)
        result["changed"] = False
        result.update(payload)
        if not payload["line_counts_match"]:
            result["failed"] = True
            result["msg"] = (
                "screen_diff_comments shifted line counts "
                f"(original={payload['original_line_count']} "
                f"screened={payload['screened_line_count']} per_file={payload['per_file']}); "
                "refusing to return an unmappable diff"
            )
        return result
