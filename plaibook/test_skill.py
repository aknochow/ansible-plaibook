# -*- coding: utf-8 -*-
"""User-level review skill install for agent CLIs."""

from __future__ import annotations

from pathlib import Path

from plaibook.skill import (
    install_user_skill,
    skill_version,
    sync_user_skill_to_stderr,
    user_skill_path,
)


def _source(tmp_path: Path, version: str = "0.1.28") -> None:
    skill = tmp_path / ".agents" / "skills" / "ansible-plaibook-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f'---\nversion: "{version}"\n---\nbody\n', encoding="utf-8")


def test_skill_version_requires_x_y_z():
    assert skill_version('version: "0.1.28"\n') == (0, 1, 28)
    assert skill_version("no version here\n") is None
    assert skill_version('version: "v0.1.28"\n') is None


def _point_at_source(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / ".agents" / "skills" / "ansible-plaibook-review" / "SKILL.md"
    monkeypatch.setattr("plaibook.skill.bundled_skill_path", lambda: source)


def test_installs_when_the_user_skill_is_missing(tmp_path, monkeypatch):
    _source(tmp_path)
    _point_at_source(monkeypatch, tmp_path)
    home = tmp_path / "home"
    assert install_user_skill(home) == "installed"
    text = user_skill_path(home).read_text(encoding="utf-8")
    assert 'version: "0.1.28"' in text


def test_updates_an_older_user_skill(tmp_path, monkeypatch):
    _source(tmp_path, "0.1.29")
    _point_at_source(monkeypatch, tmp_path)
    home = tmp_path / "home"
    dest = user_skill_path(home)
    dest.parent.mkdir(parents=True)
    dest.write_text('---\nversion: "0.1.28"\n---\nold\n', encoding="utf-8")
    assert install_user_skill(home) == "updated"
    assert "0.1.29" in dest.read_text(encoding="utf-8")


def test_keeps_an_equal_edited_or_newer_or_unversioned_skill(tmp_path, monkeypatch):
    _source(tmp_path, "0.1.28")
    _point_at_source(monkeypatch, tmp_path)
    home = tmp_path / "home"
    dest = user_skill_path(home)
    dest.parent.mkdir(parents=True)
    dest.write_text('---\nversion: "0.1.28"\n---\nhand edit\n', encoding="utf-8")
    assert install_user_skill(home) == "kept"
    assert "hand edit" in dest.read_text(encoding="utf-8")

    dest.write_text('---\nversion: "0.1.30"\n---\nnewer\n', encoding="utf-8")
    assert install_user_skill(home) == "kept"
    assert "newer" in dest.read_text(encoding="utf-8")

    dest.write_text("hand written, no version\n", encoding="utf-8")
    assert install_user_skill(home) == "kept"
    assert dest.read_text(encoding="utf-8") == "hand written, no version\n"


def test_missing_source_when_bundled_skill_is_absent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("plaibook.skill.bundled_skill_path", lambda: None)
    home = tmp_path / "home"
    assert install_user_skill(home) == "missing-source"
    assert sync_user_skill_to_stderr(home) == 1
    err = capsys.readouterr().err
    assert "missing" in err.lower()
    assert not user_skill_path(home).exists()
