# -*- coding: utf-8 -*-
"""Opt-in review skill install for the agent CLIs found on this machine."""

from __future__ import annotations

import io
import json
from argparse import Namespace

import pytest
import yaml

from plaibook import skill
from plaibook.skill import (
    ADOPT,
    CURRENT,
    ERROR,
    INSTALLED,
    LOCATIONS,
    MANIFEST_NAME,
    SKIPPED,
    STALE,
    UPDATED,
    USER_OWNED,
    detected_locations,
    skill_dir,
    skill_version,
    sync_skill,
)

BODY = b'---\nname: ansible-plaibook-review\nversion: "0.1.33"\n---\nnew\n'
_BY_KEY = {location.key: location for location in LOCATIONS}


@pytest.fixture
def home(tmp_path, monkeypatch):
    source = tmp_path / "share" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_bytes(BODY)
    monkeypatch.setattr(skill, "bundled_skill_path", lambda: source)
    monkeypatch.setattr(skill.shutil, "which", lambda name: None)
    root = tmp_path / "home"
    root.mkdir()
    return root


def _states(results):
    return {item.key: item.state for item in results}


def _plant(copy, body):
    copy.mkdir(parents=True, exist_ok=True)
    (copy / "SKILL.md").write_bytes(body)
    (copy / MANIFEST_NAME).write_text(json.dumps({"sha256": skill._sha256(body)}))


def test_skill_version_requires_x_y_z():
    assert skill_version('version: "0.1.28"\n') == (0, 1, 28)
    assert skill_version("no version here\n") is None
    assert skill_version('version: "v0.1.28"\n') is None


def test_detects_a_cli_by_command_or_config_dir(home):
    assert detected_locations(home, which=lambda name: None) == []
    (home / ".claude").mkdir()
    found = detected_locations(home, which=lambda name: "/bin/agy" if name == "agy" else None)
    assert [location.key for location in found] == ["claude-code", "antigravity"]
    (home / ".config" / "opencode").mkdir(parents=True)
    assert "agents" in [location.key for location in detected_locations(home, which=lambda name: None)]


def test_installs_only_where_a_cli_is_found(home):
    (home / ".claude").mkdir()
    assert _states(sync_skill(home)) == {"claude-code": INSTALLED}
    copy = skill_dir(_BY_KEY["claude-code"], home)
    assert (copy / "SKILL.md").read_bytes() == BODY
    assert json.loads((copy / MANIFEST_NAME).read_text())["version"] == "0.1.33"
    assert not (home / ".agents").exists()
    assert not (home / ".gemini").exists()
    assert _states(sync_skill(home)) == {"claude-code": CURRENT}


def test_replaces_plais_own_copy_when_the_install_changes_either_way(home):
    (home / ".claude").mkdir()
    copy = skill_dir(_BY_KEY["claude-code"], home)
    _plant(copy, b'---\nversion: "0.2.0"\n---\nnewer\n')
    assert _states(sync_skill(home, write=False)) == {"claude-code": STALE}
    assert _states(sync_skill(home)) == {"claude-code": UPDATED}
    assert (copy / "SKILL.md").read_bytes() == BODY


def test_never_replaces_a_copy_plai_did_not_write(home):
    (home / ".claude").mkdir()
    sync_skill(home)
    copy = skill_dir(_BY_KEY["claude-code"], home)
    (copy / "SKILL.md").write_bytes(b"hand edit\n")
    assert _states(sync_skill(home)) == {"claude-code": USER_OWNED}
    assert (copy / "SKILL.md").read_bytes() == b"hand edit\n"

    (copy / MANIFEST_NAME).unlink()
    assert _states(sync_skill(home)) == {"claude-code": USER_OWNED}
    assert (copy / "SKILL.md").read_bytes() == b"hand edit\n"


def test_adopts_an_identical_copy_with_no_manifest(home):
    (home / ".claude").mkdir()
    copy = skill_dir(_BY_KEY["claude-code"], home)
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_bytes(BODY)
    assert _states(sync_skill(home, write=False)) == {"claude-code": ADOPT}
    assert _states(sync_skill(home)) == {"claude-code": INSTALLED}
    assert (copy / MANIFEST_NAME).is_file()


def test_a_failed_manifest_write_is_repaired_on_the_next_sync(home, monkeypatch):
    (home / ".claude").mkdir()
    copy = skill_dir(_BY_KEY["claude-code"], home)
    _plant(copy, b'---\nversion: "0.1.0"\n---\nold\n')
    real_write = skill._atomic_write_bytes

    def fail_on_manifest(path, data):
        if path.name == MANIFEST_NAME:
            raise OSError("disk full")
        real_write(path, data)

    monkeypatch.setattr(skill, "_atomic_write_bytes", fail_on_manifest)
    assert _states(sync_skill(home)) == {"claude-code": ERROR}
    assert (copy / "SKILL.md").read_bytes() == BODY

    monkeypatch.setattr(skill, "_atomic_write_bytes", real_write)
    assert _states(sync_skill(home, write=False)) == {"claude-code": ADOPT}
    assert _states(sync_skill(home)) == {"claude-code": INSTALLED}
    assert _states(sync_skill(home)) == {"claude-code": CURRENT}


def test_leaves_a_symlinked_skill_alone(home, tmp_path):
    (home / ".claude" / "skills").mkdir(parents=True)
    checkout = tmp_path / "checkout" / "skill"
    checkout.mkdir(parents=True)
    (checkout / "SKILL.md").write_text("checkout copy\n")
    skill_dir(_BY_KEY["claude-code"], home).symlink_to(checkout, target_is_directory=True)
    result = sync_skill(home)[0]
    assert result.state == USER_OWNED
    assert "symlink" in result.detail
    assert (checkout / "SKILL.md").read_text() == "checkout copy\n"
    assert not (checkout / MANIFEST_NAME).exists()


def test_skips_a_skills_dir_that_links_to_another_location(home):
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".gemini" / "config").mkdir(parents=True)
    (home / ".gemini" / "config" / "skills").symlink_to(home / ".claude" / "skills", target_is_directory=True)
    assert _states(sync_skill(home)) == {"claude-code": INSTALLED, "antigravity": SKIPPED}


def test_never_writes_through_a_skills_dir_linked_outside_the_known_locations(home, tmp_path):
    dotfiles = tmp_path / "dotfiles" / "skills"
    dotfiles.mkdir(parents=True)
    (home / ".claude").mkdir()
    (home / ".claude" / "skills").symlink_to(dotfiles, target_is_directory=True)
    result = sync_skill(home)[0]
    assert result.state == USER_OWNED
    assert "symlink" in result.detail
    assert list(dotfiles.iterdir()) == []


def test_writes_through_a_skills_dir_linked_to_another_location(home):
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".gemini" / "config").mkdir(parents=True)
    (home / ".gemini" / "config" / "skills").symlink_to(home / ".claude" / "skills", target_is_directory=True)
    location = _BY_KEY["antigravity"]
    assert _states(sync_skill(home, locations=[location])) == {"antigravity": INSTALLED}
    assert (home / ".claude" / "skills" / skill.SKILL_NAME / "SKILL.md").read_bytes() == BODY


def test_missing_bundled_skill_is_an_error_and_writes_nothing(home, monkeypatch):
    (home / ".claude").mkdir()
    monkeypatch.setattr(skill, "bundled_skill_path", lambda: None)
    assert _states(sync_skill(home)) == {"bundled": ERROR}
    assert skill.sync_to_stderr(home, stderr=io.StringIO()) == 1
    assert not (home / ".claude" / "skills").exists()


def test_ask_defaults_to_yes_and_accepts_no(home):
    locations = [_BY_KEY["claude-code"]]
    assert skill.ask_to_enable(locations, home=home, stdin=io.StringIO("\n"), stderr=io.StringIO())
    assert not skill.ask_to_enable(locations, home=home, stdin=io.StringIO("n\n"), stderr=io.StringIO())
    assert not skill.ask_to_enable(locations, home=home, stdin=io.StringIO(""), stderr=io.StringIO())


def test_update_runs_the_sync_helper_isolated_from_the_working_directory(monkeypatch):
    from plaibook import cli

    seen = []

    class Done:
        returncode = 0

    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: seen.append(argv) or Done())
    cli._sync_review_skill_from_install()
    assert seen[0][1:3] == ["-I", "-c"]


def test_stale_check_reports_a_read_error(home, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise OSError("denied")

    monkeypatch.setattr(skill, "sync_skill", boom)
    assert skill.stale_copies(home) == []
    assert "denied" in capsys.readouterr().err


def test_setting_reads_enabled_disabled_or_unset():
    assert skill.setting({"plai_skill": "enabled"}) == "enabled"
    assert skill.setting({"plai_skill": "Disabled"}) == "disabled"
    assert skill.setting({"plai_skill": "maybe"}) is None
    assert skill.setting({}) is None


# plai update and review


@pytest.fixture
def cli_home(home, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("plaibook.cli.running_install_kind", lambda: "pipx")
    monkeypatch.setattr("plaibook.cli._interactive", lambda: False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    (home / ".claude").mkdir()
    return home


def _vars(tmp_path) -> dict:
    path = tmp_path / "config" / "ansible-plaibook" / "vars.yml"
    return (yaml.safe_load(path.read_text()) or {}) if path.is_file() else {}


def _update_args(**overrides) -> Namespace:
    values = {"check": False, "branch": None, "yes": True, "skill": None}
    values.update(overrides)
    return Namespace(**values)


def test_update_skill_enabled_saves_and_installs(cli_home, tmp_path, monkeypatch):
    from plaibook.cli import cmd_update

    monkeypatch.setattr("plaibook.cli._cmd_update_pipx", lambda args: pytest.fail("must not upgrade"))
    assert cmd_update(_update_args(skill="enabled")) == 0
    assert _vars(tmp_path)["plai_skill"] == "enabled"
    assert (skill_dir(_BY_KEY["claude-code"], cli_home) / "SKILL.md").read_bytes() == BODY


@pytest.mark.parametrize("kind", ["editable", "unknown"])
def test_update_skill_works_for_installs_update_refuses(cli_home, tmp_path, monkeypatch, kind):
    from plaibook.cli import cmd_update

    monkeypatch.setattr("plaibook.cli.running_install_kind", lambda: kind)
    assert cmd_update(_update_args(skill="disabled")) == 0
    assert _vars(tmp_path)["plai_skill"] == "disabled"


def test_update_skill_disabled_saves_and_leaves_copies(cli_home, tmp_path, monkeypatch):
    from plaibook.cli import cmd_update

    sync_skill(cli_home)
    monkeypatch.setattr("plaibook.cli._cmd_update_pipx", lambda args: pytest.fail("must not upgrade"))
    assert cmd_update(_update_args(skill="disabled")) == 0
    assert _vars(tmp_path)["plai_skill"] == "disabled"
    assert (skill_dir(_BY_KEY["claude-code"], cli_home) / "SKILL.md").is_file()


@pytest.mark.parametrize("value, synced", [("enabled", True), ("disabled", False), (None, False)])
def test_update_syncs_only_when_enabled(cli_home, monkeypatch, capsys, value, synced):
    from plaibook.cli import cmd_update
    from plaibook.config import save_vars

    if value:
        save_vars({"plai_skill": value})
    calls = []
    monkeypatch.setattr("plaibook.cli._cmd_update_pipx", lambda args: 0)
    monkeypatch.setattr("plaibook.cli._sync_review_skill_from_install", lambda: calls.append(1))
    assert cmd_update(_update_args()) == 0
    assert bool(calls) is synced
    assert f"Review skill: {value or 'not set'}" in capsys.readouterr().err


def test_update_does_not_sync_after_a_failed_upgrade(cli_home, monkeypatch):
    from plaibook.cli import cmd_update
    from plaibook.config import save_vars

    save_vars({"plai_skill": "enabled"})
    monkeypatch.setattr("plaibook.cli._cmd_update_pipx", lambda args: 2)
    monkeypatch.setattr("plaibook.cli._sync_review_skill_from_install", lambda: pytest.fail("upgrade failed"))
    assert cmd_update(_update_args()) == 2


def test_update_asks_once_on_a_terminal_and_saves_no(cli_home, tmp_path, monkeypatch):
    from plaibook.cli import cmd_update

    monkeypatch.setattr("plaibook.cli._interactive", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    monkeypatch.setattr("plaibook.cli._cmd_update_pipx", lambda args: 0)
    monkeypatch.setattr("plaibook.cli._sync_review_skill_from_install", lambda: pytest.fail("disabled"))
    assert cmd_update(_update_args()) == 0
    assert _vars(tmp_path)["plai_skill"] == "disabled"
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    assert cmd_update(_update_args()) == 0
    assert _vars(tmp_path)["plai_skill"] == "disabled"
    assert not (cli_home / ".claude" / "skills").exists()


def test_update_check_never_asks_or_writes(cli_home, tmp_path, monkeypatch):
    from plaibook.cli import cmd_update

    monkeypatch.setattr("plaibook.cli._interactive", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    monkeypatch.setattr("plaibook.cli._cmd_update_pipx", lambda args: 0)
    assert cmd_update(_update_args(check=True)) == 0
    assert "plai_skill" not in _vars(tmp_path)
    assert not (cli_home / ".claude" / "skills").exists()


def test_first_interactive_review_asks_and_installs(cli_home, tmp_path, monkeypatch):
    from plaibook.cli import _skill_before_review

    monkeypatch.setattr("plaibook.cli._interactive", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    _skill_before_review()
    assert _vars(tmp_path)["plai_skill"] == "enabled"
    assert (skill_dir(_BY_KEY["claude-code"], cli_home) / "SKILL.md").read_bytes() == BODY


def test_review_without_a_terminal_never_asks_or_writes(cli_home, tmp_path):
    from plaibook.cli import _skill_before_review

    _skill_before_review()
    assert "plai_skill" not in _vars(tmp_path)
    assert not (cli_home / ".claude" / "skills").exists()


def test_review_warns_about_a_stale_copy_without_rewriting_it(cli_home, capsys):
    from plaibook.cli import _skill_before_review
    from plaibook.config import save_vars

    save_vars({"plai_skill": "enabled"})
    copy = skill_dir(_BY_KEY["claude-code"], cli_home)
    old = b'---\nversion: "0.1.30"\n---\nold\n'
    _plant(copy, old)
    _skill_before_review()
    assert "out of date" in capsys.readouterr().err
    assert (copy / "SKILL.md").read_bytes() == old
