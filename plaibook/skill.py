# -*- coding: utf-8 -*-
"""Install the review skill where Cursor and Codex both load user skills."""

from __future__ import annotations

import os
import re
from pathlib import Path

SKILL_NAME = "ansible-plaibook-review"
_VERSION = re.compile(r'(?m)^version:\s*"([^"]+)"')
_PARTS = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def bundled_skill_path() -> Path | None:
    """Skill shipped in this install, or the checkout copy during development."""
    packaged = Path(__file__).resolve().parent / "share" / "skills" / SKILL_NAME / "SKILL.md"
    if packaged.is_file():
        return packaged
    checkout = Path(__file__).resolve().parents[1] / ".agents" / "skills" / SKILL_NAME / "SKILL.md"
    if checkout.is_file():
        return checkout
    return None


def user_skill_path(home: Path | None = None) -> Path:
    """User skill directory both Cursor and Codex read."""
    root = Path.home() if home is None else home
    return root / ".agents" / "skills" / SKILL_NAME / "SKILL.md"


def skill_version(text: str) -> tuple[int, int, int] | None:
    """Frontmatter ``version: "X.Y.Z"``, or None when that line is absent or not X.Y.Z."""
    match = _VERSION.search(text)
    if not match:
        return None
    parsed = _PARTS.fullmatch(match.group(1))
    if not parsed:
        return None
    return int(parsed.group(1)), int(parsed.group(2)), int(parsed.group(3))


def install_user_skill(home: Path | None = None) -> str:
    """Copy the bundled skill into ``~/.agents/skills`` when it is missing or older.

    A destination with no ``version`` line, or a newer version, is left
    alone. Returns ``installed``, ``updated``, ``kept``, or ``missing-source``.
    """
    source = bundled_skill_path()
    if source is None:
        return "missing-source"
    source_text = source.read_text(encoding="utf-8")
    source_version = skill_version(source_text)
    if source_version is None:
        return "missing-source"
    dest = user_skill_path(home)
    if dest.is_file():
        installed = skill_version(dest.read_text(encoding="utf-8"))
        if installed is None or installed >= source_version:
            return "kept"
        action = "updated"
    else:
        action = "installed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".md.tmp")
    tmp.write_text(source_text, encoding="utf-8")
    os.replace(tmp, dest)
    return action
