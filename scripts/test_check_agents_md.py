# -*- coding: utf-8 -*-
"""Check the uniform AGENTS.md shape.

Fails when AGENTS.md is missing, CLAUDE.md is not exactly ``@AGENTS.md``,
AGENTS.md does not link CONTRIBUTING.md, or a relative link in AGENTS.md
does not resolve.

Pytest collects this module (``testpaths`` includes ``scripts``). Run it
against another checkout with::

    python3 scripts/test_check_agents_md.py /path/to/repo
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
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


def _links_contributing(destinations: list[str]) -> bool:
    for dest in destinations:
        if _is_external(dest):
            path = dest.split("#", 1)[0].split("?", 1)[0]
        else:
            path = dest.split("#", 1)[0].split("?", 1)[0]
        if Path(path).name == "CONTRIBUTING.md":
            return True
    return False


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
        destinations = _link_destinations(agents.read_text(encoding="utf-8"))
        if not _links_contributing(destinations):
            errors.append("AGENTS.md does not link CONTRIBUTING.md")
        errors.extend(_unresolved_relative_links(agents, destinations))
    return errors


def test_missing_agents_md(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    errors = check_agents_md(tmp_path)
    assert "AGENTS.md is missing" in errors


def test_claude_md_must_be_agents_pointer(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "[CONTRIBUTING.md](CONTRIBUTING.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "CONTRIBUTING.md").write_text("rules\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n\nextra rule\n", encoding="utf-8")
    errors = check_agents_md(tmp_path)
    assert errors == ["CLAUDE.md must be exactly @AGENTS.md"]


def test_missing_contributing_link(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("[README.md](README.md)\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("overview\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    errors = check_agents_md(tmp_path)
    assert errors == ["AGENTS.md does not link CONTRIBUTING.md"]


def test_broken_relative_link(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "[CONTRIBUTING.md](CONTRIBUTING.md)\n[missing](docs/no-such.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "CONTRIBUTING.md").write_text("rules\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    errors = check_agents_md(tmp_path)
    assert len(errors) == 1
    assert errors[0].startswith("relative link does not resolve: docs/no-such.md -> ")


def test_external_link_is_not_required_to_exist_locally(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "[CONTRIBUTING.md](CONTRIBUTING.md)\n"
        "[umbrella](https://example.com/AGENTS.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "CONTRIBUTING.md").write_text("rules\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    assert check_agents_md(tmp_path) == []


def test_fenced_example_is_not_a_link(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "[CONTRIBUTING.md](CONTRIBUTING.md)\n\n"
        "```markdown\n[missing](no-such.md)\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "CONTRIBUTING.md").write_text("rules\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
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
