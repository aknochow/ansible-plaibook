# -*- coding: utf-8 -*-
"""Provider SDK install: skip when the hashed pin is present, pip --require-hashes otherwise."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plaibook.pip_hashed import hashed_requirements
from plaibook.provider_sdk import (
    FAMILY_HASHED_FILE,
    FAMILY_REQUIREMENTS,
    ProviderSdkError,
    ensure_provider_sdk,
)


def test_unknown_family_is_noop(monkeypatch):
    calls = []
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    ensure_provider_sdk(None)
    ensure_provider_sdk("claude_cli")
    assert calls == []


def test_skips_pip_when_pin_is_present(monkeypatch):
    calls = []
    monkeypatch.setattr("plaibook.provider_sdk._requirement_satisfied", lambda *_a: True)
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    ensure_provider_sdk("openai")
    assert calls == []


def test_cursor_pin_present_still_applies_http2_proxy_patch(monkeypatch):
    patched = []
    monkeypatch.setattr("plaibook.provider_sdk._requirement_satisfied", lambda *_a: True)
    monkeypatch.setattr(
        "plaibook.cursor_http2_proxy.patch_installed_cursor_sdk",
        lambda python: patched.append(python),
    )
    ensure_provider_sdk("cursor")
    assert patched


def test_installs_missing_openai_from_hashed_file(monkeypatch):
    recorded = []
    present = {"n": 0}

    def fake_satisfied(*_a):
        present["n"] += 1
        return present["n"] > 1

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.provider_sdk._requirement_satisfied", fake_satisfied)
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", fake_run)
    ensure_provider_sdk("openai", stderr=None)
    assert recorded[0][1:4] == ["-m", "pip", "install"]
    assert "--require-hashes" in recorded[0]
    assert recorded[0][-2:] == ["-r", str(hashed_requirements("openai-requirements.txt"))]
    assert all(not arg.startswith("openai>") for arg in recorded[0])


def test_installs_claude_hashed_file_when_either_dist_missing(monkeypatch):
    recorded = []
    state = {"n": 0}

    def fake_satisfied(*_a):
        state["n"] += 1
        return state["n"] > 2

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.provider_sdk._requirement_satisfied", fake_satisfied)
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", fake_run)
    ensure_provider_sdk("claude", stderr=None)
    assert recorded[0][1:4] == ["-m", "pip", "install"]
    assert "--require-hashes" in recorded[0]
    assert recorded[0][-1].endswith("claude-requirements.txt")
    assert all("anthropic[" not in arg and not arg.startswith("claude-agent-sdk>") for arg in recorded[0])


def test_installs_when_importable_but_wrong_version(monkeypatch):
    from plaibook.pip_hashed import pinned_versions

    recorded = []
    pin = pinned_versions("openai-requirements.txt")["openai"]
    versions = {"openai": "0.28.1"}

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        versions["openai"] = pin
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.provider_sdk._module_present", lambda _py, _name: True)
    monkeypatch.setattr("plaibook.provider_sdk._dist_version", lambda _py, dist: versions.get(dist))
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", fake_run)
    ensure_provider_sdk("openai", stderr=None)
    assert recorded
    assert "--require-hashes" in recorded[0]


def test_in_range_sdk_does_not_pip_install(monkeypatch):
    from plaibook.pip_hashed import pinned_versions

    pin = pinned_versions("cursor-requirements.txt")["cursor-sdk"]
    recorded = []
    monkeypatch.setattr("plaibook.provider_sdk._module_present", lambda *_a: True)

    def version(_py, dist):
        return pin if dist == "cursor-sdk" else None

    monkeypatch.setattr("plaibook.provider_sdk._dist_version", version)
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(
        "plaibook.cursor_http2_proxy.patch_installed_cursor_sdk",
        lambda python: None,
    )
    ensure_provider_sdk("cursor", stderr=None)
    assert recorded == []


def test_externally_managed_interpreter_does_not_pip_install(monkeypatch):
    monkeypatch.setattr("plaibook.provider_sdk._requirement_satisfied", lambda *_a: False)
    monkeypatch.setattr("plaibook.provider_sdk.interpreter_is_externally_managed", lambda *_a: True)
    monkeypatch.setattr(
        "plaibook.provider_sdk.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("pip must not run")),
    )
    with pytest.raises(ProviderSdkError, match="pipx"):
        ensure_provider_sdk("cursor", stderr=None)


def test_pip_failure_surfaces(monkeypatch):
    monkeypatch.setattr("plaibook.provider_sdk._requirement_satisfied", lambda *_a: False)
    monkeypatch.setattr("plaibook.provider_sdk.interpreter_is_externally_managed", lambda *_a: False)
    monkeypatch.setattr(
        "plaibook.provider_sdk.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no matching distribution"),
    )
    with pytest.raises(ProviderSdkError, match="no matching distribution"):
        ensure_provider_sdk("openai")


def test_pip_install_holds_the_interpreter_lock(monkeypatch, tmp_path):
    import subprocess
    import sys

    import plaibook.provider_sdk as sdk

    real_run = subprocess.run
    monkeypatch.setattr(sdk, "last_run_dir", lambda home=None: tmp_path)
    monkeypatch.setattr(sdk, "interpreter_is_externally_managed", lambda *_a: False)
    monkeypatch.setattr(sdk, "_patch_cursor_http2_proxy", lambda *_a, **_k: None)
    state = {"checks": 0}

    def fake_satisfied(*_a):
        state["checks"] += 1
        return state["checks"] > 1

    held = []

    def fake_run(cmd, **kwargs):
        lock_path = sdk.provider_sdk_lock_path(sys.executable)
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
            timeout=5,
        )
        held.append(probe.returncode)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sdk, "_requirement_satisfied", fake_satisfied)
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", fake_run)
    ensure_provider_sdk("cursor", stderr=None)
    assert held == [2]
    assert sdk.provider_sdk_lock_path(sys.executable).is_file()
    assert sdk.provider_sdk_lock_path("/usr/bin/python3") != sdk.provider_sdk_lock_path("/other/bin/python3")


def test_lock_path_follows_a_path_lookup(monkeypatch, tmp_path):
    import stat

    import plaibook.provider_sdk as sdk

    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "pybin"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir))
    assert sdk.provider_sdk_lock_path("pybin") == sdk.provider_sdk_lock_path(str(exe.resolve()))


def test_hashed_files_pin_every_family_distribution():
    from plaibook.pip_hashed import pinned_versions

    for family, reqs in FAMILY_REQUIREMENTS.items():
        pins = pinned_versions(FAMILY_HASHED_FILE[family])
        for _mod, dist in reqs:
            assert dist.replace("_", "-").lower() in pins
            assert pins[dist.replace("_", "-").lower()]
