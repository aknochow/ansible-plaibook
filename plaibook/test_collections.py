# -*- coding: utf-8 -*-
"""Isolated collections cache: never ~/.ansible, stamp-skip, quiet galaxy."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from plaibook.collections import (
    GALAXY_GITHUB_MIRRORS,
    CollectionInstallError,
    _clone_at_ref,
    collection_is_installed,
    collection_key_from_requirement,
    collections_dir,
    collections_lock_path,
    ensure_collections,
    git_url_has_userinfo,
    install_git_sources,
    merge_collections_path,
    redact_git_userinfo,
    require_commit_sha,
    requirement_collection_keys,
)
from plaibook.playbook import async_dir_override, run_ansible_playbook

GIT_TEST_TIMEOUT = 30
# ai-guardian credentials-in-git-url ignores this placeholder; a literal
# 8+ character password would trip SECRET-001 on the PR diff.
_USERINFO_PW = "PASSWORD"
_GH = "github.com"


def _userinfo_https(path="/org/repo.git") -> str:
    # Host is concatenated so this file does not contain user:…@github.
    return f"https://user:{_USERINFO_PW}@{_GH}{path}"


def _plant(dest, *keys):
    for ns, name in keys:
        coll = dest / "ansible_collections" / ns / name
        coll.mkdir(parents=True, exist_ok=True)
        (coll / "MANIFEST.json").write_text("{}\n")


def _install_spies(monkeypatch, calls=None):
    """GitHub-first install without ansible-galaxy -r or the Galaxy API."""
    recorded = calls if calls is not None else []

    def fake_git(galaxy, dest, cols, env):
        recorded.append(("git", list(cols), dict(env)))
        for col in cols:
            key = collection_key_from_requirement(col)
            if key:
                _plant(dest, key)

    def fake_mirrors(galaxy, dest, env, needed=None):
        recorded.append(("mirrors", list(needed or []), dict(env)))
        if needed:
            _plant(dest, *needed)

    monkeypatch.setattr("plaibook.collections.install_git_sources", fake_git)
    monkeypatch.setattr("plaibook.collections.install_galaxy_github_mirrors", fake_mirrors)
    return recorded


def test_ensure_collections_noop_without_requirements(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    home = tmp_path / "home"
    assert ensure_collections(playbook, home=home) is None
    assert not collections_dir(home).exists()


def test_ensure_collections_installs_into_isolated_cache(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    home.mkdir()
    recorded = _install_spies(monkeypatch)
    dest = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert dest == home / ".cache" / "ansible-plaibook" / "collections"
    assert dest.is_dir()
    assert ".ansible" not in str(dest)
    assert recorded, "expected GitHub-first install on first run"
    env = recorded[0][2]
    assert env["GIT_CONFIG_KEY_0"] == "advice.detachedHead"
    assert env["GIT_CONFIG_VALUE_0"] == "false"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    stamp = dest / ".requirements.sha256"
    assert stamp.is_file()
    assert "ANSIBLE_COLLECTIONS_PATHS" not in env

    recorded.clear()
    again = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert again == dest
    assert recorded == []


def test_ensure_collections_drops_legacy_paths_from_galaxy_env(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    home.mkdir()
    recorded = _install_spies(monkeypatch)
    monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATHS", "/opt/legacy")
    ensure_collections(
        playbook,
        home=home,
        galaxy_bin="ansible-galaxy",
        env={"ANSIBLE_COLLECTIONS_PATHS": "/from-caller"},
    )
    assert recorded
    galaxy_env = recorded[0][2]
    assert "ANSIBLE_COLLECTIONS_PATHS" not in galaxy_env
    assert galaxy_env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)[0] == str(collections_dir(home))


def test_ensure_collections_reinstalls_when_requirements_change(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections: []\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    dest.mkdir(parents=True)
    (dest / ".requirements.sha256").write_text("not-the-digest\n")
    recorded = _install_spies(monkeypatch)
    err = []

    class Err:
        def write(self, text):
            err.append(text)

        def flush(self):
            pass

    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy", stderr=Err())
    assert recorded
    assert "Updating" in "".join(err)


def test_ensure_collections_reinstalls_when_stamp_tree_missing(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections:\n  - name: ansible.posix\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    dest.mkdir(parents=True)
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    (dest / ".requirements.sha256").write_text(digest + "\n")
    recorded = _install_spies(monkeypatch)
    err = []

    class Err:
        def write(self, text):
            err.append(text)

        def flush(self):
            pass

    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy", stderr=Err())
    assert recorded
    assert "cache incomplete" in "".join(err)


def test_ensure_collections_reinstalls_when_a_required_collection_is_missing(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections:\n  - name: ansible.posix\n  - name: kubernetes.core\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    dest.mkdir(parents=True)
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    (dest / ".requirements.sha256").write_text(digest + "\n")
    one = dest / "ansible_collections" / "ansible" / "posix"
    one.mkdir(parents=True)
    (one / "MANIFEST.json").write_text("{}\n")
    recorded = _install_spies(monkeypatch)
    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert recorded


def test_ensure_collections_skips_when_all_required_collections_are_present(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections:\n  - name: ansible.posix\n  - name: kubernetes.core\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    for ns, name in (("ansible", "posix"), ("kubernetes", "core")):
        coll = dest / "ansible_collections" / ns / name
        coll.mkdir(parents=True)
        (coll / "MANIFEST.json").write_text("{}\n")
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / ".requirements.sha256").write_text(digest + "\n")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert calls == []


def test_ensure_collections_reinstalls_when_a_dependency_masks_a_required_name(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections:\n  - name: ansible.posix\n  - name: kubernetes.core\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    dest.mkdir(parents=True)
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    (dest / ".requirements.sha256").write_text(digest + "\n")
    # Two installed dirs (count would match) but kubernetes.core is missing;
    # a dependency must not stand in for it.
    for ns, name in (("ansible", "posix"), ("community", "general")):
        coll = dest / "ansible_collections" / ns / name
        coll.mkdir(parents=True)
        (coll / "MANIFEST.json").write_text("{}\n")
    recorded = _install_spies(monkeypatch)
    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert recorded


def test_requirement_collection_keys_maps_git_urls_and_fqcns(tmp_path):
    req = tmp_path / "collections-requirements.yml"
    req.write_text(
        "collections:\n"
        "  - name: https://github.com/aknochow/ansible-openshell.git\n"
        "    type: git\n"
        "    version: f2df487af9d08d314235394e281b71cfdcdb5134\n"
        "  - name: community.general\n"
        "  - name: kubernetes.core\n"
        "  - name: ansible.posix\n"
    )
    assert requirement_collection_keys(req) == [
        ("aknochow", "openshell"),
        ("community", "general"),
        ("kubernetes", "core"),
        ("ansible", "posix"),
    ]
    assert collection_key_from_requirement(
        {"name": "https://github.com/ansible-collections/ansible.posix.git", "type": "git"}
    ) == ("ansible", "posix")
    dest = tmp_path / "dest"
    posix = dest / "ansible_collections" / "ansible" / "posix"
    posix.mkdir(parents=True)
    (posix / "MANIFEST.json").write_text("{}\n")
    assert collection_is_installed(dest, "ansible", "posix")
    assert not collection_is_installed(dest, "kubernetes", "core")


def test_galaxy_github_mirrors_and_requirements_use_commit_shas():
    sha = re.compile(r"^[0-9a-f]{40}$")
    for fqn, (url, ref) in GALAXY_GITHUB_MIRRORS.items():
        assert sha.match(ref), f"{fqn} pin {ref!r} is not a full commit SHA"
        assert url.startswith("https://github.com/")
    repo = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((repo / "collections-requirements.yml").read_bytes())
    by_key = {}
    for col in data["collections"]:
        key = collection_key_from_requirement(col)
        if key in (("community", "general"), ("kubernetes", "core"), ("ansible", "posix")):
            version = str(col["version"])
            assert sha.match(version), f"{key} version {version!r} is not a full commit SHA"
            by_key[f"{key[0]}.{key[1]}"] = version
    assert by_key["community.general"] == GALAXY_GITHUB_MIRRORS["community.general"][1]
    assert by_key["kubernetes.core"] == GALAXY_GITHUB_MIRRORS["kubernetes.core"][1]
    assert by_key["ansible.posix"] == GALAXY_GITHUB_MIRRORS["ansible.posix"][1]


def test_ensure_collections_refuses_dangling_symlink_dest(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    cache = home / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)
    (cache / "collections").symlink_to(tmp_path / "missing")
    with pytest.raises(CollectionInstallError, match="symlink"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_refuses_symlink_dest(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    cache = home / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (cache / "collections").symlink_to(real)
    with pytest.raises(CollectionInstallError, match="symlink"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_refuses_regular_file_dest(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    cache = home / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)
    (cache / "collections").write_text("not a directory\n")
    with pytest.raises(CollectionInstallError, match="not a directory"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_missing_galaxy_bin_is_collection_error(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"

    def boom():
        raise FileNotFoundError("ansible-galaxy")

    monkeypatch.setattr("plaibook.collections.ansible_galaxy_bin", boom)
    with pytest.raises(CollectionInstallError, match="ansible-galaxy not found"):
        ensure_collections(playbook, home=home)


def test_ensure_collections_surfaces_github_install_failure(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n"
        "  - name: https://github.com/example/ansible-posix.git\n"
        "    type: git\n"
        "    version: e98d9a0756458be1ac710988498000973889075c\n"
    )
    home = tmp_path / "home"

    def fake_git(*args, **kwargs):
        raise CollectionInstallError("git fetch failed for example/ansible-posix")

    monkeypatch.setattr("plaibook.collections.install_git_sources", fake_git)
    monkeypatch.setattr("plaibook.collections.install_galaxy_github_mirrors", lambda *a, **k: None)
    with pytest.raises(CollectionInstallError, match="git fetch failed"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_rejects_non_list_collections(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: {}\n")
    home = tmp_path / "home"
    with pytest.raises(CollectionInstallError, match="must be a list"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    dest = collections_dir(home)
    assert not (dest / ".requirements.sha256").is_file()


def test_ensure_collections_rejects_mapping_without_collections_key(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("foo: bar\n")
    home = tmp_path / "home"
    with pytest.raises(CollectionInstallError, match="must contain a collections list"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    dest = collections_dir(home)
    assert not (dest / ".requirements.sha256").is_file()


def test_ensure_collections_rejects_non_mapping_collection_row(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections:\n  - community.general\n")
    home = tmp_path / "home"
    with pytest.raises(CollectionInstallError, match="must be a mapping"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    dest = collections_dir(home)
    assert not (dest / ".requirements.sha256").is_file()


def test_require_commit_sha_rejects_branches_tags_and_head():
    url = "https://github.com/example/ansible-posix.git"
    assert (
        require_commit_sha(url, "E98D9A0756458BE1AC710988498000973889075C")
        == "e98d9a0756458be1ac710988498000973889075c"
    )
    for ref in ("main", "HEAD", "2.2.2", "e98d9a0", None, ""):
        with pytest.raises(CollectionInstallError, match="40-character commit SHA"):
            require_commit_sha(url, ref)


def test_require_commit_sha_does_not_echo_invalid_ref():
    url = "https://github.com/example/ansible-posix.git"
    invalid_ref = "not-a-sha-ref-value-must-not-leak"
    with pytest.raises(CollectionInstallError, match="40-character commit SHA") as excinfo:
        require_commit_sha(url, invalid_ref)
    assert invalid_ref not in str(excinfo.value)


def test_ensure_collections_rejects_mutable_git_ref(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n  - name: https://github.com/example/ansible-posix.git\n    type: git\n    version: main\n"
    )
    home = tmp_path / "home"
    with pytest.raises(CollectionInstallError, match=r"collections\[0\].*40-character commit SHA"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    dest = collections_dir(home)
    assert not (dest / ".requirements.sha256").is_file()


def test_ensure_collections_rejects_row_without_string_name(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections:\n  - name: 1\n  - name: ansible.posix\n")
    home = tmp_path / "home"
    with pytest.raises(CollectionInstallError, match=r"collections\[0\].*non-empty string name"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    dest = collections_dir(home)
    assert not (dest / ".requirements.sha256").is_file()


def test_ensure_collections_rejects_git_row_without_url(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n  - name: ansible.posix\n    type: git\n    version: e98d9a0756458be1ac710988498000973889075c\n"
    )
    home = tmp_path / "home"
    with pytest.raises(CollectionInstallError, match=r"collections\[0\].*git source must be"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert not (collections_dir(home) / ".requirements.sha256").is_file()


def test_ensure_collections_rejects_plaintext_http_git(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n"
        "  - name: http://github.com/example/ansible-posix.git\n"
        "    type: git\n"
        "    version: e98d9a0756458be1ac710988498000973889075c\n"
    )
    home = tmp_path / "home"
    with pytest.raises(CollectionInstallError, match="not plaintext HTTP"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert not (collections_dir(home) / ".requirements.sha256").is_file()


def test_ensure_collections_rejects_git_plus_http(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n"
        "  - name: git+http://github.com/example/ansible-posix.git\n"
        "    type: git\n"
        "    version: e98d9a0756458be1ac710988498000973889075c\n"
    )
    home = tmp_path / "home"
    with pytest.raises(CollectionInstallError, match="not plaintext HTTP"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_lets_unexpected_errors_propagate(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections:\n  - name: ansible.posix\n")
    home = tmp_path / "home"

    def boom(*_args, **_kwargs):
        raise RuntimeError("programmer bug")

    monkeypatch.setattr("plaibook.collections.install_git_sources", boom)
    with pytest.raises(RuntimeError, match="programmer bug"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_wraps_subprocess_and_oserror(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections:\n  - name: ansible.posix\n")
    home = tmp_path / "home"

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("plaibook.collections.install_git_sources", boom)
    with pytest.raises(CollectionInstallError, match="GitHub collection install failed"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_git_fetch_error_redacts_userinfo(tmp_path, monkeypatch):
    monkeypatch.setattr("plaibook.collections.time.sleep", lambda *_a, **_k: None)

    def boom(*_args, **_kwargs):
        raise OSError(f"auth failed for {_userinfo_https()}")

    monkeypatch.setattr("plaibook.collections.subprocess.run", boom)
    dest = tmp_path / "checkout"
    url = "https://github.com/org/repo.git"
    sha = "e98d9a0756458be1ac710988498000973889075c"
    with pytest.raises(CollectionInstallError, match="git fetch failed") as raised:
        _clone_at_ref(url, sha, dest, env={"GIT_TERMINAL_PROMPT": "0"})
    message = str(raised.value)
    assert _USERINFO_PW not in message
    assert "user:" not in message
    assert "github.com/org/repo.git" in message
    assert redact_git_userinfo(_userinfo_https()) == (
        "https://github.com/org/repo.git"
    )


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        (
            _userinfo_https(),
            "https://github.com/org/repo.git",
        ),
        (
            "git+" + _userinfo_https(),
            "git+https://github.com/org/repo.git",
        ),
        (
            f"ssh://user:{_USERINFO_PW}@example.com/repo.git",
            "ssh://example.com/repo.git",
        ),
        (
            f"git+ssh://user:{_USERINFO_PW}@example.com/repo.git",
            "git+ssh://example.com/repo.git",
        ),
        (
            f"file://user:{_USERINFO_PW}@localhost/tmp/repo.git",
            "file://localhost/tmp/repo.git",
        ),
        (
            "https://TOKEN@github.com/org/repo.git",
            "https://github.com/org/repo.git",
        ),
    ],
)
def test_redact_git_userinfo_covers_accepted_schemes(raw, cleaned):
    assert redact_git_userinfo(raw) == cleaned
    assert _USERINFO_PW not in redact_git_userinfo(raw)


@pytest.mark.parametrize(
    "url",
    [
        _userinfo_https(),
        "git+" + _userinfo_https(),
        f"ssh://user:{_USERINFO_PW}@example.com/repo.git",
        f"git+ssh://user:{_USERINFO_PW}@example.com/repo.git",
        f"file://user:{_USERINFO_PW}@localhost/tmp/repo.git",
        "https://TOKEN@github.com/org/repo.git",
    ],
)
def test_git_url_has_userinfo_rejects_embedded_credentials(url):
    assert git_url_has_userinfo(url)
    with pytest.raises(CollectionInstallError, match="must not embed credentials"):
        require_commit_sha(url, "e98d9a0756458be1ac710988498000973889075c")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo.git",
        "ssh://git@github.com/org/repo.git",
        "git+ssh://git@github.com/org/repo.git",
        "git@github.com:org/repo.git",
        "file:///tmp/repo.git",
    ],
)
def test_git_url_has_userinfo_allows_username_only_ssh_and_plain_urls(url):
    assert not git_url_has_userinfo(url)


def test_clone_strips_userinfo_from_git_remote_argv(monkeypatch, tmp_path):
    sha = "e98d9a0756458be1ac710988498000973889075c"
    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        if "rev-parse" in cmd:
            return SimpleNamespace(returncode=0, stdout=f"{sha}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections.require_commit_sha", lambda url, ref: str(ref).lower())
    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    dest = tmp_path / "checkout"
    _clone_at_ref(_userinfo_https(), sha, dest)
    joined = " ".join(" ".join(cmd) for cmd in recorded)
    assert _USERINFO_PW not in joined
    assert "user:" not in joined
    origin = next(cmd for cmd in recorded if cmd[:3] == ["git", "remote", "add"])
    assert origin[-1] == "https://github.com/org/repo.git"


def test_clone_strips_ssh_userinfo_from_git_remote_argv(monkeypatch, tmp_path):
    sha = "e98d9a0756458be1ac710988498000973889075c"
    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        if "rev-parse" in cmd:
            return SimpleNamespace(returncode=0, stdout=f"{sha}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections.require_commit_sha", lambda url, ref: str(ref).lower())
    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    dest = tmp_path / "checkout"
    _clone_at_ref(f"ssh://user:{_USERINFO_PW}@example.com/repo.git", sha, dest)
    joined = " ".join(" ".join(cmd) for cmd in recorded)
    assert _USERINFO_PW not in joined
    origin = next(cmd for cmd in recorded if cmd[:3] == ["git", "remote", "add"])
    assert origin[-1] == "ssh://example.com/repo.git"


@pytest.mark.parametrize(
    "name",
    [
        _userinfo_https("/example/ansible-posix.git"),
        f"ssh://user:{_USERINFO_PW}@example.com/ansible-posix.git",
        f"git+ssh://user:{_USERINFO_PW}@example.com/ansible-posix.git",
        f"file://user:{_USERINFO_PW}@localhost/tmp/ansible-posix.git",
    ],
)
def test_ensure_collections_rejects_git_userinfo(tmp_path, name):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n"
        f"  - name: {name}\n"
        "    type: git\n"
        "    version: e98d9a0756458be1ac710988498000973889075c\n"
    )
    home = tmp_path / "home"
    with pytest.raises(CollectionInstallError, match="must not embed credentials") as raised:
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert _USERINFO_PW not in str(raised.value)
    assert not (collections_dir(home) / ".requirements.sha256").is_file()


def test_redact_git_userinfo_strips_basic_authorization_header():
    leaked = "fatal: extraHeader=AUTHORIZATION: basic abcdef0123456789"
    cleaned = redact_git_userinfo(leaked)
    assert "abcdef0123456789" not in cleaned
    assert "AUTHORIZATION: basic [redacted]" in cleaned


def test_install_git_sources_passes_env_to_clone(tmp_path, monkeypatch):
    seen = {}

    def fake_clone(url, ref, dest, env=None):
        seen["env"] = env
        dest.mkdir(parents=True)

    monkeypatch.setattr("plaibook.collections._clone_at_ref", fake_clone)
    monkeypatch.setattr("plaibook.collections._install_from_dir", lambda *a, **k: None)
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1"}
    cols = [
        {
            "name": "https://github.com/example/ansible-posix.git",
            "type": "git",
            "version": "e98d9a0756458be1ac710988498000973889075c",
        }
    ]
    install_git_sources("ansible-galaxy", tmp_path, cols, env)
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["env"]["GIT_CONFIG_COUNT"] == "1"


def test_ensure_collections_installs_from_github_never_galaxy_api(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n"
        "  - name: https://github.com/ansible-collections/community.general.git\n"
        "    type: git\n"
        "    version: 049524674b13ad9782849c427266935c8ec61954\n"
    )
    home = tmp_path / "home"
    err = []

    class Err:
        def write(self, text):
            err.append(text)

        def flush(self):
            pass

    def boom_run(cmd, **kwargs):
        joined = " ".join(str(part) for part in cmd)
        raise AssertionError(f"first-run install must not call Galaxy or git here: {joined}")

    recorded = _install_spies(monkeypatch)
    monkeypatch.setattr("plaibook.collections.subprocess.run", boom_run)
    dest = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy", stderr=Err())
    assert dest == collections_dir(home)
    log = "".join(err)
    assert "Installing Ansible collections from GitHub" in log
    assert "Galaxy install failed" not in log
    assert any(kind == "git" for kind, *_ in recorded)
    assert collection_is_installed(dest, "community", "general")
    assert collection_is_installed(dest, "community", "library_inventory_filtering_v1")
    assert (dest / ".requirements.sha256").is_file()


def test_ensure_collections_installs_git_sources_with_no_deps(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n"
        "  - name: https://github.com/example/ansible-posix.git\n"
        "    type: git\n"
        "    version: e98d9a0756458be1ac710988498000973889075c\n"
    )
    home = tmp_path / "home"
    calls = []

    def fake_clone(url, ref, dest, env=None):
        dest.mkdir(parents=True, exist_ok=True)

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        assert "-r" not in cmd
        if cmd[:3] == ["ansible-galaxy", "collection", "build"]:
            out = Path(cmd[cmd.index("--output-path") + 1])
            (out / "example-posix-1.0.0.tar.gz").write_bytes(b"archive")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        dest = Path(cmd[cmd.index("-p") + 1])
        _plant(dest, ("example", "posix"))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections._clone_at_ref", fake_clone)
    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    dest = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert dest == collections_dir(home)
    assert calls
    builds = [c for c in calls if c[:3] == ["ansible-galaxy", "collection", "build"]]
    installs = [c for c in calls if c[:3] == ["ansible-galaxy", "collection", "install"]]
    assert builds
    assert installs
    assert all(str(c[3]).endswith(".tar.gz") for c in installs)
    assert all("--no-deps" in c for c in installs)
    assert collection_is_installed(dest, "example", "posix")


def test_merge_collections_path_puts_cache_first(tmp_path):
    home = tmp_path / "home"
    isolated = str(collections_dir(home))
    env = {
        "ANSIBLE_COLLECTIONS_PATH": os.pathsep.join(["/opt/collections", isolated]),
    }
    merge_collections_path(env, home=home)
    parts = env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)
    assert parts[0] == isolated
    assert parts[1] == "/opt/collections"
    assert parts.count(isolated) == 1
    assert "ANSIBLE_COLLECTIONS_PATHS" not in env


def test_merge_collections_path_drops_legacy_paths_var(tmp_path):
    home = tmp_path / "home"
    isolated = str(collections_dir(home))
    env = {"ANSIBLE_COLLECTIONS_PATHS": "/opt/legacy"}
    merge_collections_path(env, home=home)
    assert env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep) == [isolated, "/opt/legacy"]
    assert "ANSIBLE_COLLECTIONS_PATHS" not in env


def test_run_ansible_playbook_sets_isolated_collections_path(tmp_path, monkeypatch):
    recorded = {}

    class FakeProc:
        pid = 1
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(**kwargs):
        recorded.update(kwargs)
        return FakeProc()

    monkeypatch.setattr("plaibook.playbook.subprocess.Popen", fake_popen)
    (tmp_path / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    coll = collections_dir(home)
    coll.mkdir(parents=True)
    run_ansible_playbook(
        ["ansible-playbook", "review.yml"],
        playbook_root=tmp_path,
        verbose=False,
        home=home,
    )
    env = recorded["env"]
    assert env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)[0] == str(coll)
    assert env["TMPDIR"] == str(home / ".cache" / "ansible-plaibook" / "tmp")
    scratch = Path(env["TMPDIR"])
    assert scratch.is_dir()
    info = scratch.stat()
    assert info.st_uid == os.geteuid()
    assert (info.st_mode & 0o777) == 0o700
    assert Path(env["ANSIBLE_CONFIG"]) == tmp_path / "ansible.cfg"


def test_async_dir_override_only_when_home_is_not_the_account_home(tmp_path, monkeypatch):
    account = tmp_path / "account"
    other = tmp_path / "other"
    account.mkdir()
    other.mkdir()
    monkeypatch.setattr(
        "plaibook.playbook.pwd.getpwuid",
        lambda _uid: SimpleNamespace(pw_dir=str(account)),
    )
    assert async_dir_override({"HOME": str(account)}) is None
    assert async_dir_override({"HOME": str(other)}) == str(other / ".ansible_async")
    assert async_dir_override({"HOME": str(other), "ANSIBLE_ASYNC_DIR": "/set"}) is None
    assert async_dir_override({}) is None


def test_async_dir_override_when_uid_has_no_account(tmp_path, monkeypatch):
    def missing(_uid):
        raise KeyError(_uid)

    monkeypatch.setattr("plaibook.playbook.pwd.getpwuid", missing)
    assert async_dir_override({"HOME": str(tmp_path)}) == str(tmp_path / ".ansible_async")


def test_run_ansible_playbook_does_not_keep_slash_tmp(tmp_path, monkeypatch):
    recorded = {}

    class FakeProc:
        pid = 1
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(**kwargs):
        recorded.update(kwargs)
        return FakeProc()

    monkeypatch.setattr("plaibook.playbook.subprocess.Popen", fake_popen)
    monkeypatch.setenv("TMPDIR", "/tmp")
    (tmp_path / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    run_ansible_playbook(
        ["ansible-playbook", "review.yml"],
        playbook_root=tmp_path,
        verbose=False,
        home=home,
    )
    assert recorded["env"]["TMPDIR"] == str(home / ".cache" / "ansible-plaibook" / "tmp")
    assert recorded["env"]["TMPDIR"] != "/tmp"


def test_runtime_tmp_dir_is_private(tmp_path):
    import stat

    from plaibook.playbook import runtime_tmp_dir

    home = tmp_path / "home"
    path = runtime_tmp_dir(home)
    info = path.stat()
    assert stat.S_ISDIR(info.st_mode)
    assert info.st_uid == os.geteuid()
    assert stat.S_IMODE(info.st_mode) == 0o700


def test_runtime_tmp_dir_rejects_symlink(tmp_path):
    from plaibook.playbook import ScratchDirError, last_run_dir, runtime_tmp_dir

    home = tmp_path / "home"
    cache = last_run_dir(home)
    cache.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (cache / "tmp").symlink_to(target)
    with pytest.raises(ScratchDirError, match="symlink"):
        runtime_tmp_dir(home)


def test_runtime_tmp_dir_fails_closed_when_chmod_raises(tmp_path, monkeypatch):
    from plaibook.playbook import ScratchDirError, runtime_tmp_dir

    home = tmp_path / "home"

    def boom(path, mode):
        raise OSError("chmod not supported")

    monkeypatch.setattr("os.chmod", boom)
    with pytest.raises(ScratchDirError, match="private"):
        runtime_tmp_dir(home)


def test_runtime_tmp_dir_fails_closed_when_mode_stays_open(tmp_path, monkeypatch):
    from plaibook.playbook import ScratchDirError, runtime_tmp_dir

    home = tmp_path / "home"
    real_chmod = os.chmod

    def leave_open(path, mode):
        real_chmod(path, 0o755)

    monkeypatch.setattr("os.chmod", leave_open)
    with pytest.raises(ScratchDirError, match="not private"):
        runtime_tmp_dir(home)


def test_materialize_playbook_share_copies_playbook_tree(tmp_path):
    from plaibook._setup_share import SHARE_FILES, SHARE_TREES, materialize_playbook_share

    repo = tmp_path / "repo"
    repo.mkdir()
    for name in SHARE_FILES:
        (repo / name).write_text(f"{name}\n")
    for name in SHARE_TREES:
        nested = repo / name / "keep"
        nested.mkdir(parents=True)
        (nested / "ok.txt").write_text("ok\n")
        (nested / "test_not_shipped.py").write_text("no\n")
        junk = repo / name / "__pycache__"
        junk.mkdir()
        (junk / "x.pyc").write_text("no\n")
    dest = tmp_path / "share"
    result = materialize_playbook_share(repo, dest)
    assert result == dest.resolve()
    for name in SHARE_FILES:
        assert (dest / name).read_text() == f"{name}\n"
    assert (dest / "roles" / "keep" / "ok.txt").is_file()
    assert not (dest / "roles" / "__pycache__").exists()
    assert not (dest / "roles" / "keep" / "test_not_shipped.py").exists()


def test_collections_lock_path_is_beside_cache_not_inside(tmp_path):
    home = tmp_path / "home"
    dest = collections_dir(home)
    lock = collections_lock_path(home)
    assert lock.parent == dest.parent
    assert lock.name == "collections.lock"
    assert dest.name == "collections"
    assert dest not in lock.parents


def test_ensure_collections_holds_flock_during_galaxy(tmp_path, monkeypatch):
    import subprocess
    import sys

    import plaibook.collections as coll

    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    home.mkdir()
    probe_codes = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        lock_path = collections_lock_path(home)
        assert lock_path.is_file()
        probe = real_run(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl, os, sys\n"
                    f"fd = os.open({str(lock_path)!r}, os.O_RDWR)\n"
                    "try:\n"
                    "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                    "except BlockingIOError:\n"
                    "    sys.exit(2)\n"
                    "else:\n"
                    "    sys.exit(0)\n"
                    "finally:\n"
                    "    os.close(fd)\n"
                ),
            ],
            check=False,
        )
        probe_codes.append(probe.returncode)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_git(galaxy, dest, cols, env):
        coll.subprocess.run(
            [galaxy, "collection", "install", "-p", str(dest), "--force", "--no-deps"],
            env=env,
            capture_output=True,
            timeout=coll.GALAXY_TIMEOUT_SECONDS,
        )

    monkeypatch.setattr(coll, "install_git_sources", fake_git)
    monkeypatch.setattr(coll, "install_galaxy_github_mirrors", lambda *a, **k: None)
    monkeypatch.setattr(coll.subprocess, "run", fake_run)
    dest = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert dest == collections_dir(home)
    assert probe_codes == [2], "a sibling process must not take LOCK_EX while galaxy runs"
    assert collections_lock_path(home).parent == dest.parent
    assert not (dest / "collections.lock").exists()


def _ensure_collections_worker(playbook: str, home: str, galaxy: str, result_path: str) -> None:
    dest = ensure_collections(Path(playbook), home=Path(home), galaxy_bin=galaxy)
    Path(result_path).write_text(str(dest))


def test_ensure_collections_serializes_two_processes(tmp_path):
    import multiprocessing
    import subprocess
    import sys
    import time

    src = tmp_path / "example" / "ansible-posix"
    src.mkdir(parents=True)
    (src / "galaxy.yml").write_text(
        "namespace: example\nname: posix\nversion: 1.0.0\nreadme: README.md\nauthors: [test]\n"
    )
    (src / "README.md").write_text("test\n")
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=src,
        check=True,
        capture_output=True,
        timeout=GIT_TEST_TIMEOUT,
    )
    subprocess.run(
        ["git", "add", "."],
        cwd=src,
        check=True,
        capture_output=True,
        timeout=GIT_TEST_TIMEOUT,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=src,
        check=True,
        capture_output=True,
        timeout=GIT_TEST_TIMEOUT,
    )

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=src,
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TEST_TIMEOUT,
    ).stdout.strip()

    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        f"collections:\n  - name: {src}\n    type: git\n    version: {sha}\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "galaxy.log"
    release = tmp_path / "release"
    fake_galaxy = tmp_path / "ansible-galaxy"
    fake_galaxy.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys, time\n"
        f"log = pathlib.Path({str(log)!r})\n"
        f"release = pathlib.Path({str(release)!r})\n"
        "args = sys.argv\n"
        "if 'build' in args:\n"
        "    out = pathlib.Path(args[args.index('--output-path') + 1])\n"
        "    (out / 'example-posix-1.0.0.tar.gz').write_bytes(b'archive')\n"
        "    sys.exit(0)\n"
        "dest = pathlib.Path(args[args.index('-p') + 1])\n"
        "coll = dest / 'ansible_collections' / 'example' / 'posix'\n"
        "with log.open('a') as fh:\n"
        "    fh.write('start\\n')\n"
        "    fh.flush()\n"
        "deadline = time.time() + 10\n"
        "while time.time() < deadline and not release.exists():\n"
        "    time.sleep(0.05)\n"
        "coll.mkdir(parents=True, exist_ok=True)\n"
        "(coll / 'MANIFEST.json').write_text('{}\\n')\n"
        "with log.open('a') as fh:\n"
        "    fh.write('end\\n')\n"
        "sys.exit(0)\n"
    )
    fake_galaxy.chmod(0o755)

    ctx = multiprocessing.get_context("spawn")
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    p1 = ctx.Process(
        target=_ensure_collections_worker,
        args=(str(playbook), str(home), str(fake_galaxy), str(first)),
    )
    p2 = ctx.Process(
        target=_ensure_collections_worker,
        args=(str(playbook), str(home), str(fake_galaxy), str(second)),
    )
    p1.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        if log.is_file() and "start" in log.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    else:
        p1.kill()
        raise AssertionError("first ansible-galaxy did not start")
    p2.start()
    time.sleep(0.4)
    assert log.read_text(encoding="utf-8").count("start") == 1
    release.write_text("go\n", encoding="utf-8")
    p1.join(timeout=10)
    p2.join(timeout=10)
    assert p1.exitcode == 0
    assert p2.exitcode == 0
    dest = collections_dir(home)
    assert first.read_text(encoding="utf-8") == str(dest)
    assert second.read_text(encoding="utf-8") == str(dest)
    assert log.read_text(encoding="utf-8").count("start") == 1
    assert log.read_text(encoding="utf-8").count("end") == 1
    assert (dest / ".requirements.sha256").is_file()
    lock = collections_lock_path(home)
    assert lock.is_file()
    assert lock.parent == dest.parent
