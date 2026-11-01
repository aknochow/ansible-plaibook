# -*- coding: utf-8 -*-
"""CI collection installer: named cache keys and subprocess timeouts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from plaibook.collections import GALAXY_TIMEOUT_SECONDS, requirement_collection_keys

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_ci_install():
    spec = importlib.util.spec_from_file_location(
        "ci_install_collections",
        REPO_ROOT / "scripts" / "ci-install-collections.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_repo_requirements_keys_are_named_not_a_raw_count():
    keys = requirement_collection_keys(REPO_ROOT / "collections-requirements.yml")
    assert ("aknochow", "openshell") in keys
    assert ("aknochow", "cursor") in keys
    assert ("community", "general") in keys
    assert ("kubernetes", "core") in keys
    assert ("ansible", "posix") in keys
    # YAML rows only — the community.general GitHub-mirror dep is added by
    # _required_keys() / _runtime_required_keys(), not the requirements file.
    assert ("community", "library_inventory_filtering_v1") not in keys
    assert len(keys) == 9


def test_install_from_dir_builds_then_installs_archive(monkeypatch, tmp_path):
    import plaibook.collections as coll

    mod = _load_ci_install()
    timeouts = []

    def fake_run(cmd, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        if "build" in cmd:
            out = Path(cmd[cmd.index("--output-path") + 1])
            (out / "ns-name-1.0.0.tar.gz").write_bytes(b"x")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(coll.subprocess, "run", fake_run)
    src = tmp_path / "src"
    src.mkdir()
    mod._install_from_dir("ansible-galaxy", src, tmp_path / "dest")
    assert timeouts
    assert all(t == GALAXY_TIMEOUT_SECONDS for t in timeouts)


def test_git_auth_uses_config_env_not_argv(monkeypatch, tmp_path):
    mod = _load_ci_install()
    recorded = {}

    def fake_run(cmd, **kwargs):
        recorded["cmd"] = list(cmd)
        recorded["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._git(["status"], cwd=tmp_path, token="ci-test-token")
    joined = " ".join(recorded["cmd"])
    assert "ci-test-token" not in joined
    assert "ci-test-token@" not in joined
    assert "insteadOf" not in joined
    assert "bearer" not in joined.lower()
    assert any(arg.startswith("--config-env=") and "extraHeader" in arg for arg in recorded["cmd"])
    assert all("AUTHORIZATION:" not in arg for arg in recorded["cmd"])
    assert not any(arg.startswith("-c") and "extraHeader" in arg for arg in recorded["cmd"])
    env = recorded["kwargs"]["env"]
    assert env[mod.GIT_AUTH_HEADER_ENV].startswith("AUTHORIZATION: basic ")
    assert "ci-test-token" not in env[mod.GIT_AUTH_HEADER_ENV]
    assert recorded["kwargs"]["timeout"] == GALAXY_TIMEOUT_SECONDS
    assert mod._GIT_EXTRAHEADER_KEY == "http.https://github.com/.extraHeader"
    extra = [arg for arg in recorded["cmd"] if arg.startswith("--config-env=")][0]
    assert extra == f"--config-env={mod._GIT_EXTRAHEADER_KEY}={mod.GIT_AUTH_HEADER_ENV}"


def test_git_extraheader_key_source_is_not_an_https_url_literal():
    src = (REPO_ROOT / "scripts" / "ci-install-collections.py").read_text(encoding="utf-8")
    assert 'scheme="https"' in src
    assert '"http.https://"' not in src
    assert "http.https://" + "github.com" not in src


def test_git_passes_timeout(monkeypatch, tmp_path):
    mod = _load_ci_install()
    recorded = {}

    def fake_run(cmd, **kwargs):
        recorded["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._git(["status"], cwd=tmp_path, token=None)
    assert recorded["kwargs"]["timeout"] == GALAXY_TIMEOUT_SECONDS


def test_required_keys_include_mirror_dependency():
    mod = _load_ci_install()
    keys = mod._required_keys()
    assert ("community", "library_inventory_filtering_v1") in keys
    assert ("ansible", "posix") in keys
    assert ("community", "general") in keys
    assert len(keys) == 10


def test_ci_installer_has_no_galaxy_requirements_path():
    mod = _load_ci_install()
    assert not hasattr(mod, "try_requirements_file")


def test_install_git_sources_rejects_fqcn_typed_as_git(tmp_path):
    mod = _load_ci_install()
    cols = [
        {
            "name": "ansible.posix",
            "type": "git",
            "version": "e98d9a0756458be1ac710988498000973889075c",
        }
    ]
    with pytest.raises(SystemExit, match="git source must be"):
        mod.install_git_sources("ansible-galaxy", tmp_path, cols, token=None)
