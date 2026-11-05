# -*- coding: utf-8 -*-
"""Install the review skill for the agent CLIs found on this machine.

Opt-in. ``plai_skill`` in the operator vars.yml is ``enabled`` or
``disabled``. The first interactive plai run with no value asks once and
saves the answer. Nothing is written while it is unset or disabled.

Locations, and the CLIs that read them:

- ``~/.agents/skills``: Codex, Cursor, OpenCode, pi
- ``~/.claude/skills``: Claude Code (also read by Cursor and OpenCode)
- ``~/.gemini/config/skills``: Antigravity (``agy``)

A location is used only when one of its CLIs is installed. Each copy has a
manifest with the hash plai wrote. plai replaces only a copy that still has
that hash. A hand-edited copy, a copy with no manifest, and a symlink are
reported and left alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, TextIO

SKILL_NAME = "ansible-plaibook-review"
MANIFEST_NAME = ".plaibook-managed.json"
SETTING = "plai_skill"
ENABLED = "enabled"
DISABLED = "disabled"
_VERSION = re.compile(r'(?m)^version:\s*"([^"]+)"')
_PARTS = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

CURRENT = "current"  # matches this install
INSTALLED = "installed"  # written by this sync
UPDATED = "updated"  # replaced by this sync
STALE = "stale"  # plai's copy, different from this install
ADOPT = "adopt"  # same content as this install, no manifest yet
MISSING = "missing"
USER_OWNED = "user-owned"  # not plai's; never replaced
SKIPPED = "skipped"  # same directory as another location
ERROR = "error"
_OK = frozenset({CURRENT, INSTALLED, UPDATED, SKIPPED})


@dataclass(frozen=True)
class Location:
    """A user skills directory and the CLIs that read it."""

    key: str
    label: str
    skills_dir: tuple[str, ...]
    commands: tuple[str, ...]
    config_dirs: tuple[tuple[str, ...], ...]


LOCATIONS = (
    Location(
        "claude-code",
        "Claude Code",
        (".claude", "skills"),
        ("claude",),
        ((".claude",),),
    ),
    Location(
        "antigravity",
        "Antigravity (agy)",
        (".gemini", "config", "skills"),
        ("agy",),
        ((".gemini", "config"),),
    ),
    Location(
        "agents",
        "Codex, Cursor, OpenCode, pi",
        (".agents", "skills"),
        ("codex", "cursor-agent", "opencode", "pi"),
        ((".codex",), (".cursor",), (".config", "opencode"), (".pi",)),
    ),
)


@dataclass(frozen=True)
class TargetStatus:
    """What a sync found, or did, at one location."""

    key: str
    label: str
    path: Path
    state: str
    detail: str = ""


def bundled_skill_path() -> Path | None:
    """Skill shipped in this install's package data.

    A checkout's ``.agents/skills`` copy is never a source. Copying it would
    carry an unreviewed checkout's agent instructions into the user-wide
    skills directory, where every project's agent sessions load them.
    """
    packaged = Path(__file__).resolve().parent / "share" / "skills" / SKILL_NAME / "SKILL.md"
    return packaged if packaged.is_file() else None


def _home(home: Path | None) -> Path:
    return Path.home() if home is None else home


def skill_dir(location: Location, home: Path | None = None) -> Path:
    return _home(home).joinpath(*location.skills_dir) / SKILL_NAME


def detected_locations(
    home: Path | None = None,
    *,
    which: Callable[[str], str | None] | None = None,
) -> list[Location]:
    """Locations whose CLI is on PATH or has its config directory."""
    root = _home(home)
    which = which or shutil.which
    found = []
    for location in LOCATIONS:
        if any(which(name) for name in location.commands) or any(
            root.joinpath(*parts).is_dir() for parts in location.config_dirs
        ):
            found.append(location)
    return found


def skill_version(text: str) -> tuple[int, int, int] | None:
    """Frontmatter ``version: "X.Y.Z"``, or None when that line is absent or not X.Y.Z."""
    match = _VERSION.search(text)
    if not match:
        return None
    parsed = _PARTS.fullmatch(match.group(1))
    if not parsed:
        return None
    return int(parsed.group(1)), int(parsed.group(2)), int(parsed.group(3))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``path`` via an exclusive temp file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".skill-", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _read_manifest(directory: Path) -> dict | None:
    try:
        data = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("sha256"), str) else None


def _copy_status(directory: Path, source: bytes) -> tuple[str, str]:
    """State of one copy, before any write."""
    if directory.is_symlink():
        return USER_OWNED, f"symlink to {os.readlink(directory)}"
    if directory.exists() and not directory.is_dir():
        return USER_OWNED, "not a directory"
    skill_md = directory / "SKILL.md"
    if not skill_md.exists() and not skill_md.is_symlink():
        if directory.exists() and any(directory.iterdir()):
            return USER_OWNED, "directory has files but no SKILL.md"
        return MISSING, ""
    if skill_md.is_symlink() or not skill_md.is_file():
        return USER_OWNED, "SKILL.md is not a regular file"
    current = skill_md.read_bytes()
    manifest = _read_manifest(directory)
    if manifest is None:
        if current == source:
            return ADOPT, "matches this install, no manifest"
        return USER_OWNED, "no plaibook manifest"
    if manifest["sha256"] != _sha256(current):
        return USER_OWNED, "edited since plai wrote it"
    if current == source:
        return CURRENT, ""
    return STALE, "differs from this install"


def _bundled() -> tuple[bytes, str] | TargetStatus:
    source_path = bundled_skill_path()
    if source_path is None:
        return TargetStatus("bundled", "bundled skill", Path("share") / "skills" / SKILL_NAME, ERROR, "missing")
    source = source_path.read_bytes()
    match = _VERSION.search(source.decode("utf-8", errors="replace"))
    if not match or _PARTS.fullmatch(match.group(1)) is None:
        return TargetStatus("bundled", "bundled skill", source_path, ERROR, "no valid version")
    return source, match.group(1)


def sync_skill(
    home: Path | None = None,
    *,
    write: bool = True,
    locations: list[Location] | None = None,
) -> list[TargetStatus]:
    """Bring each detected location in line with this install's skill.

    With ``write=False`` nothing changes and each status says what a sync
    would find. Copies plai does not own are never replaced.
    """
    bundled = _bundled()
    if isinstance(bundled, TargetStatus):
        return [bundled]
    source, version = bundled
    manifest = (json.dumps({"sha256": _sha256(source), "version": version}, indent=2) + "\n").encode("utf-8")

    results: list[TargetStatus] = []
    seen: set[Path] = set()
    for location in detected_locations(home) if locations is None else locations:
        directory = skill_dir(location, home)
        parent = directory.parent.resolve()
        if parent in seen:
            results.append(
                TargetStatus(location.key, location.label, directory, SKIPPED, "same directory as another location")
            )
            continue
        seen.add(parent)
        state, detail = _copy_status(directory, source)
        if write and state in (MISSING, STALE, ADOPT):
            try:
                _atomic_write_bytes(directory / "SKILL.md", source)
                _atomic_write_bytes(directory / MANIFEST_NAME, manifest)
                state = UPDATED if state == STALE else INSTALLED
                detail = ""
            except OSError as exc:
                state, detail = ERROR, str(exc)
        results.append(TargetStatus(location.key, location.label, directory, state, detail))
    return results


def _line(item: TargetStatus) -> str:
    text = f"  {item.label}: {item.state} ({item.path})"
    if item.detail:
        text += f": {item.detail}"
    return text


def print_results(results: list[TargetStatus], *, stderr: TextIO, quiet_ok: bool = False) -> None:
    for item in results:
        if quiet_ok and item.state in (CURRENT, SKIPPED):
            continue
        stderr.write(_line(item) + "\n")
    if any(item.state == USER_OWNED for item in results):
        stderr.write(
            "  plai leaves copies it did not write alone. Remove one to let plai manage it.\n"
        )


def setting(vars_data: Mapping[str, object]) -> str | None:
    """``enabled``, ``disabled``, or None when unset or unrecognized."""
    value = str(vars_data.get(SETTING) or "").strip().lower()
    return value if value in (ENABLED, DISABLED) else None


def ask_to_enable(
    locations: list[Location],
    *,
    home: Path | None = None,
    stdin: TextIO,
    stderr: TextIO,
) -> bool:
    """Ask once whether to install the skill for the detected CLIs."""
    stderr.write("plai can install its review skill for the agent CLIs on this machine:\n")
    for location in locations:
        stderr.write(f"  {location.label}: {skill_dir(location, home)}\n")
    stderr.write(
        f"Saved as {SETTING} in vars.yml; plai update keeps it current. Install it? [Y/n] "
    )
    stderr.flush()
    try:
        answer = stdin.readline().strip().lower()
    except EOFError:
        answer = "n"
    return answer in ("", "y", "yes")


def sync_to_stderr(home: Path | None = None, *, stderr: TextIO = sys.stderr) -> int:
    """Sync and print what changed. Exit 1 when a location is not in sync."""
    try:
        results = sync_skill(home)
    except OSError as exc:
        stderr.write(f"Could not install the plaibook review skill: {exc}\n")
        return 1
    if any(item.state not in (CURRENT, SKIPPED) for item in results):
        stderr.write("Review skill:\n")
        print_results(results, stderr=stderr, quiet_ok=True)
    return 0 if all(item.state in _OK for item in results) else 1


def stale_copies(home: Path | None = None) -> list[TargetStatus]:
    """plai's own copies that differ from this install. Never writes."""
    try:
        return [item for item in sync_skill(home, write=False) if item.state == STALE]
    except OSError:
        return []
