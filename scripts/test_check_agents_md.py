# -*- coding: utf-8 -*-
"""Check the uniform AGENTS.md shape.

Fails when AGENTS.md is missing, CLAUDE.md is not exactly ``@AGENTS.md``,
AGENTS.md does not link CONTRIBUTING.md with a relative link, a relative
link in AGENTS.md does not resolve, or the first fenced block under
``## Commands`` does not appear exactly in CONTRIBUTING.md.

Run it against another checkout with::

    python3 path/to/test_check_agents_md.py /path/to/repo
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _repo_root() -> Path:
    """Directory that holds this check's AGENTS.md, walking up from the file."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "AGENTS.md").is_file() or (parent / ".git").exists():
            return parent
    return here.parents[1]


REPO_ROOT = _repo_root()
_LINK_RE = re.compile(r"\[[^\[\]]*\]\(([^)]*)\)")
_FENCE_RE = re.compile(r"^```", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Drop fenced code blocks so example text is not treated as a link."""
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line.lstrip()):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def _link_destinations(text: str) -> list[str]:
    destinations: list[str] = []
    for match in _LINK_RE.finditer(_strip_fences(text)):
        dest = match.group(1).strip()
        if dest.startswith("<") and ">" in dest:
            dest = dest[1 : dest.index(">")]
        if dest.startswith(("'", '"')):
            continue
        # Optional markdown title: (url "title")
        dest = dest.split()[0] if dest else dest
        destinations.append(dest)
    return destinations


def _claude_is_agents_pointer(text: str) -> bool:
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    return text == "@AGENTS.md"


def _is_external(dest: str) -> bool:
    return dest.startswith(("http://", "https://", "mailto:", "//"))


def _links_contributing(destinations: list[str], agents_md: Path) -> bool:
    """True only for a relative link to this checkout's root CONTRIBUTING.md.

    ``./CONTRIBUTING.md`` and ``CONTRIBUTING.md#section`` count. A different
    file with the same name, a parent-directory link, and an external URL
    that ends in that filename do not.
    """
    required = (agents_md.parent / "CONTRIBUTING.md").resolve()
    for dest in destinations:
        if not dest or dest.startswith("#") or _is_external(dest):
            continue
        path = dest.split("#", 1)[0].split("?", 1)[0]
        if not path or path.startswith("/"):
            continue
        if (agents_md.parent / path).resolve() == required:
            return True
    return False


def _commands_section(text: str) -> str | None:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == "## Commands":
            start = index + 1
            break
    if start is None:
        return None
    section: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        section.append(line)
    return "\n".join(section)


def _first_fenced_block(section: str) -> str | None:
    lines = section.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            start = index
            break
    if start is None:
        return None
    for index in range(start + 1, len(lines)):
        if lines[index].lstrip().startswith("```"):
            return "\n".join(lines[start : index + 1])
    return None


def _commands_block_errors(agents_text: str, contributing_text: str | None) -> list[str]:
    section = _commands_section(agents_text)
    if section is None:
        return ["AGENTS.md has no ## Commands section"]
    block = _first_fenced_block(section)
    if block is None:
        return ["AGENTS.md has no fenced block under ## Commands"]
    source = "" if contributing_text is None else contributing_text.replace("\r\n", "\n")
    if block.replace("\r\n", "\n") not in source:
        return ["the first fenced block under ## Commands does not appear in CONTRIBUTING.md"]
    return []


def _unresolved_relative_links(agents_md: Path, destinations: list[str]) -> list[str]:
    errors: list[str] = []
    base = agents_md.parent
    for dest in destinations:
        if not dest or dest.startswith("#") or _is_external(dest):
            continue
        path_part = dest.split("#", 1)[0].split("?", 1)[0]
        if not path_part:
            continue
        resolved = (base / path_part).resolve()
        if not resolved.exists():
            errors.append(f"relative link does not resolve: {dest} -> {resolved}")
    return errors


def check_agents_md(root: Path) -> list[str]:
    """Return human-readable failures. An empty list means the tree passes."""
    root = root.resolve()
    errors: list[str] = []
    agents = root / "AGENTS.md"
    claude = root / "CLAUDE.md"

    if not agents.is_file():
        errors.append("AGENTS.md is missing")
    if not claude.is_file():
        errors.append("CLAUDE.md is missing")
    elif not _claude_is_agents_pointer(claude.read_text(encoding="utf-8")):
        errors.append("CLAUDE.md must be exactly @AGENTS.md")

    if agents.is_file():
        agents_text = agents.read_text(encoding="utf-8")
        destinations = _link_destinations(agents_text)
        if not _links_contributing(destinations, agents):
            errors.append("AGENTS.md does not link CONTRIBUTING.md")
        errors.extend(_unresolved_relative_links(agents, destinations))
        contributing = root / "CONTRIBUTING.md"
        contributing_text = contributing.read_text(encoding="utf-8") if contributing.is_file() else None
        errors.extend(_commands_block_errors(agents_text, contributing_text))
    return errors


def test_missing_agents_md(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    errors = check_agents_md(tmp_path)
    assert "AGENTS.md is missing" in errors


_COMMANDS = "```bash\nuv run pytest\n```"


def _write_tree(
    tmp_path: Path,
    agents: str,
    contributing: str | None = None,
    claude: str = "@AGENTS.md\n",
) -> None:
    (tmp_path / "AGENTS.md").write_text(agents, encoding="utf-8")
    if contributing is not None:
        (tmp_path / "CONTRIBUTING.md").write_text(contributing, encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(claude, encoding="utf-8")


def _agents_with_commands(extra: str = "") -> str:
    return (
        "[CONTRIBUTING.md](CONTRIBUTING.md)\n\n"
        "## Commands\n\n"
        f"{_COMMANDS}\n"
        f"{extra}"
    )


def test_claude_md_must_be_agents_pointer(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        _agents_with_commands(),
        f"rules\n\n{_COMMANDS}\n",
        claude="@AGENTS.md\n\nextra rule\n",
    )
    errors = check_agents_md(tmp_path)
    assert errors == ["CLAUDE.md must be exactly @AGENTS.md"]


def test_missing_contributing_link(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        f"[README.md](README.md)\n\n## Commands\n\n{_COMMANDS}\n",
        f"rules\n\n{_COMMANDS}\n",
    )
    (tmp_path / "README.md").write_text("overview\n", encoding="utf-8")
    errors = check_agents_md(tmp_path)
    assert errors == ["AGENTS.md does not link CONTRIBUTING.md"]


def test_broken_relative_link(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        _agents_with_commands("[missing](docs/no-such.md)\n"),
        f"rules\n\n{_COMMANDS}\n",
    )
    errors = check_agents_md(tmp_path)
    assert len(errors) == 1
    assert errors[0].startswith("relative link does not resolve: docs/no-such.md -> ")


def test_external_link_is_not_required_to_exist_locally(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        _agents_with_commands(
            "[umbrella](https://example.com/AGENTS.md)\n"
        ),
        f"rules\n\n{_COMMANDS}\n",
    )
    assert check_agents_md(tmp_path) == []


def test_dot_slash_and_anchor_contributing_links_count(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        "[CONTRIBUTING.md](./CONTRIBUTING.md#branch-names)\n\n" f"## Commands\n\n{_COMMANDS}\n",
        f"rules\n\n{_COMMANDS}\n",
    )
    assert check_agents_md(tmp_path) == []


def test_nested_contributing_link_does_not_count(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CONTRIBUTING.md").write_text("other\n", encoding="utf-8")
    _write_tree(
        tmp_path,
        "[CONTRIBUTING.md](docs/CONTRIBUTING.md)\n\n" f"## Commands\n\n{_COMMANDS}\n",
        f"rules\n\n{_COMMANDS}\n",
    )
    assert check_agents_md(tmp_path) == ["AGENTS.md does not link CONTRIBUTING.md"]


def test_parent_contributing_link_does_not_count(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        "[CONTRIBUTING.md](../CONTRIBUTING.md)\n\n" f"## Commands\n\n{_COMMANDS}\n",
        f"rules\n\n{_COMMANDS}\n",
    )
    errors = check_agents_md(tmp_path)
    assert "AGENTS.md does not link CONTRIBUTING.md" in errors


def test_external_contributing_link_does_not_count(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        "[CONTRIBUTING.md](https://github.com/aknochow/ansible-plaibook/blob/main/CONTRIBUTING.md)\n\n"
        f"## Commands\n\n{_COMMANDS}\n",
        f"rules\n\n{_COMMANDS}\n",
    )
    errors = check_agents_md(tmp_path)
    assert errors == ["AGENTS.md does not link CONTRIBUTING.md"]


def test_commands_fence_must_match_contributing(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        _agents_with_commands(),
        "```bash\nuv run pytest -q\n```\n",
    )
    errors = check_agents_md(tmp_path)
    assert errors == ["the first fenced block under ## Commands does not appear in CONTRIBUTING.md"]


def test_fenced_example_is_not_a_link(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        _agents_with_commands("```markdown\n[missing](no-such.md)\n```\n"),
        f"rules\n\n{_COMMANDS}\n",
    )
    assert check_agents_md(tmp_path) == []


def test_this_repository() -> None:
    errors = check_agents_md(REPO_ROOT)
    assert errors == []


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO_ROOT
    errors = check_agents_md(root)
    if errors:
        print(f"check_agents_md failed for {root}:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"check_agents_md passed for {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
