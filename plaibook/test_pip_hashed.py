# -*- coding: utf-8 -*-
"""Hashed requirements files used by runtime pip --require-hashes installs."""

from __future__ import annotations

from plaibook.pip_hashed import hashed_requirements, pinned_versions, pip_install_hashed_argv


def test_hashed_requirements_exist():
    for name in (
        "openai-requirements.txt",
        "claude-requirements.txt",
        "gemini-requirements.txt",
        "cursor-requirements.txt",
        "openshell-requirements.txt",
        "sandbox-runtime-requirements.txt",
        "build-backend-requirements.txt",
    ):
        path = hashed_requirements(name)
        text = path.read_text(encoding="utf-8")
        assert "--hash=sha256:" in text
        assert "==" in text


def test_pip_install_hashed_argv_require_hashes():
    argv = pip_install_hashed_argv("/usr/bin/python3", "openshell-requirements.txt")
    assert argv[:4] == ["/usr/bin/python3", "-m", "pip", "install"]
    assert "--require-hashes" in argv
    assert argv[-2:] == ["-r", str(hashed_requirements("openshell-requirements.txt"))]
    dry = pip_install_hashed_argv("/usr/bin/python3", "openshell-requirements.txt", dry_run=True)
    assert "--dry-run" in dry
    assert "--require-hashes" in dry


def test_pinned_versions_reads_top_level_dists():
    pins = pinned_versions("openshell-requirements.txt")
    assert pins["openshell"] == "0.1.3"
    openai = pinned_versions("openai-requirements.txt")
    assert "openai" in openai
    runtime = pinned_versions("sandbox-runtime-requirements.txt")
    assert "ansible-core" in runtime
    assert "cursor-sdk" in runtime
    assert "jinja2" in runtime
    assert "pyyaml" in runtime
    backend = pinned_versions("build-backend-requirements.txt")
    assert "setuptools" in backend
    assert "wheel" in backend


def test_lock_digest_changes_when_file_changes():
    from plaibook import pip_hashed

    first = pip_hashed.lock_digest("openshell-requirements.txt")
    second = pip_hashed.lock_digest("openshell-requirements.txt", "sandbox-runtime-requirements.txt")
    assert first != second
    assert len(first) == 64
