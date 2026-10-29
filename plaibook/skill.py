# -*- coding: utf-8 -*-
"""Install the review skill where agent CLIs load user skills."""

from __future__ import annotations

import os
import re
import sys
import tempfile
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
    """User skill path agent CLIs read from ``~/.agents/skills``."""
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


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``path`` via an exclusive temp file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".skill-", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def sync_user_skill_to_stderr(home: Path | None = None) -> int:
    """Install or update the user skill; print status to stderr. Exit 0 on success."""
    try:
        action = install_user_skill(home)
    except OSError as exc:
        print(f"Could not install the plaibook review skill: {exc}", file=sys.stderr)
        return 1
    dest = user_skill_path(home)
    if action == "installed":
        print(f"Installed the review skill at {dest}", file=sys.stderr)
    elif action == "updated":
        print(f"Updated the review skill at {dest}", file=sys.stderr)
    elif action == "missing-source":
        print(
            "Could not sync the plaibook review skill: bundled skill is missing "
            "or has no valid version in frontmatter.",
            file=sys.stderr,
        )
        return 1
    return 0


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
    _atomic_write_text(dest, source_text)
    return action
