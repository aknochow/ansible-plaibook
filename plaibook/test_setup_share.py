# -*- coding: utf-8 -*-
"""Every path the wheel build vendors must be in the sdist.

PyPI wheels are built from the sdist, not from a checkout. A source path
that _setup_share reads but MANIFEST.in leaves out builds from a checkout
and fails from the sdist.
"""
from __future__ import annotations

from pathlib import Path

from plaibook._setup_share import SHARE_FILES, SHARE_TREES, SKILL_RELATIVE

ROOT = Path(__file__).resolve().parent.parent


def _manifest() -> tuple[set[str], set[str]]:
    includes, grafts = set(), set()
    for line in (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "include":
            includes.update(parts[1:])
        elif len(parts) == 2 and parts[0] == "graft":
            grafts.add(parts[1])
    return includes, grafts


def test_every_vendored_file_is_in_the_sdist():
    includes, _ = _manifest()
    missing = [name for name in (*SHARE_FILES, str(SKILL_RELATIVE)) if name not in includes]
    assert missing == []


def test_every_vendored_tree_is_in_the_sdist():
    _, grafts = _manifest()
    assert [name for name in SHARE_TREES if name not in grafts] == []
