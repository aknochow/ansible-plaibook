# -*- coding: utf-8 -*-
"""OpenShell SDK pin, and the Python 3.11 runtime used when plai is 3.10."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from plaibook.openshell_sdk import (
    HASHED_REQUIREMENTS,
    LOCK_NAME,
    RUNTIME_HASHED_REQUIREMENTS,
    SDK_SPEC,
    OpenshellSdkError,
    _source_fingerprint,
    ensure_openshell_sdk,
    find_sdk_python,
    plaibook_install_spec,
    prepare_sandbox_runtime,
    reexec_sandbox_runtime,
    sdk_satisfies,
    spec_from_direct_url,
    version_satisfies,
)
from plaibook.pip_hashed import hashed_requirements


@pytest.fixture(autouse=True)
def _openshell_interpreter_has_pip(monkeypatch):
    monkeypatch.setattr("plaibook.pip_hashed.python_has_pip", lambda _python: True)


def _capable(monkeypatch):
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)


@pytest.mark.parametrize(
    ("version", "ok"),
    [
        ("0.0.0a0", False),
        ("0.0.115", False),
        ("0.0.116", False),
        ("0.1.1", False),
        ("0.1.2", False),
        ("0.1.3", True),
        ("0.1.9", True),
        ("0.1.9+local", True),
        ("0.2.0", False),
        ("0.1.3rc1", False),
    ],
)
def test_version_satisfies_pin(version, ok):
    assert version_satisfies(version) is ok


def test_ensure_openshell_sdk_skips_pip_when_already_in_range(monkeypatch):
    calls = []
    _capable(monkeypatch)
    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", lambda: True)
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    ensure_openshell_sdk()
    assert calls == []


def test_ensure_openshell_sdk_replaces_editable_stub(monkeypatch):
    recorded = []
    checks = {"n": 0}

    def fake_satisfies():
        checks["n"] += 1
        return checks["n"] > 1

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    _capable(monkeypatch)
    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", fake_satisfies)
    monkeypatch.setattr("plaibook.openshell_sdk._package_present", lambda _exe: True)
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    ensure_openshell_sdk(stderr=None)
    assert recorded[0][1:5] == ["-m", "pip", "install", "--disable-pip-version-check"]
    assert "--dry-run" in recorded[0]
    assert "--require-hashes" in recorded[0]
    assert recorded[0][-1] == str(hashed_requirements(HASHED_REQUIREMENTS))
    assert recorded[1][1:4] == ["-m", "pip", "uninstall"]
    assert recorded[2][1:4] == ["-m", "pip", "install"]
    assert "--dry-run" not in recorded[2]
    assert "--require-hashes" in recorded[2]
    assert recorded[2][-1] == str(hashed_requirements(HASHED_REQUIREMENTS))
    assert SDK_SPEC not in recorded[2]


def test_ensure_openshell_sdk_dry_run_failure_does_not_uninstall(monkeypatch):
    recorded = []
    _capable(monkeypatch)
    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", lambda: False)
    monkeypatch.setattr("plaibook.openshell_sdk._package_present", lambda _exe: True)

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        if "--dry-run" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="no matching distribution")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    with pytest.raises(OpenshellSdkError, match="no matching distribution"):
        ensure_openshell_sdk()
    assert all("uninstall" not in cmd for cmd in recorded)


def test_ensure_installs_without_uninstall_when_package_absent(monkeypatch):
    recorded = []
    checks = {"n": 0}

    def fake_satisfies():
        checks["n"] += 1
        return checks["n"] > 1

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    _capable(monkeypatch)
    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", fake_satisfies)
    monkeypatch.setattr("plaibook.openshell_sdk._package_present", lambda _exe: False)
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    ensure_openshell_sdk(stderr=None)
    assert recorded[0][1:4] == ["-m", "pip", "install"]
    assert "--dry-run" not in recorded[0]
    assert "--require-hashes" in recorded[0]
    assert recorded[0][-1] == str(hashed_requirements(HASHED_REQUIREMENTS))
    assert all("uninstall" not in cmd for cmd in recorded)


def test_ensure_refuses_python_310_without_calling_pip(monkeypatch):
    calls = []
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: False)
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_version", lambda _exe: (3, 10, 11))
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    with pytest.raises(OpenshellSdkError, match="3.11"):
        ensure_openshell_sdk()
    assert calls == []


def test_spec_from_direct_url_pins_git_commit():
    spec = spec_from_direct_url(
        {
            "url": "https://github.com/aknochow/ansible-plaibook.git",
            "vcs_info": {"vcs": "git", "commit_id": "abc123", "requested_revision": "some-branch"},
        },
        "0.1.0",
    )
    assert spec == "git+https://github.com/aknochow/ansible-plaibook.git@abc123"


def test_spec_from_direct_url_strips_userinfo():
    spec = spec_from_direct_url(
        {
            "url": "https://user:PASSWORD@" + "github.com/aknochow/ansible-plaibook.git",
            "vcs_info": {"vcs": "git", "commit_id": "abc123"},
        },
        "0.1.0",
    )
    assert spec == "git+https://github.com/aknochow/ansible-plaibook.git@abc123"
    assert "PASSWORD" not in spec
    assert "user:" not in spec


def test_spec_from_direct_url_strips_ssh_userinfo():
    spec = spec_from_direct_url(
        {
            "url": "ssh://user:PASSWORD@example.com/aknochow/ansible-plaibook.git",
            "vcs_info": {"vcs": "git", "commit_id": "abc123"},
        },
        "0.1.0",
    )
    assert spec == "git+ssh://example.com/aknochow/ansible-plaibook.git@abc123"
    assert "PASSWORD" not in spec
    assert "user:" not in spec


def test_spec_from_direct_url_strips_git_plus_ssh_userinfo():
    spec = spec_from_direct_url(
        {
            "url": "git+ssh://user:PASSWORD@example.com/aknochow/ansible-plaibook.git",
            "vcs_info": {"vcs": "git", "commit_id": "abc123"},
        },
        "0.1.0",
    )
    assert spec == "git+ssh://example.com/aknochow/ansible-plaibook.git@abc123"
    assert "PASSWORD" not in spec


def test_spec_from_direct_url_file_not_pypi():
    assert spec_from_direct_url({"url": "file:///tmp/plaibook"}, "0.1.0") == "/tmp/plaibook"
    with pytest.raises(OpenshellSdkError, match="PyPI version pin"):
        spec_from_direct_url({"url": "https://files.pythonhosted.org/plaibook.whl"}, "0.1.0")


def test_plaibook_install_spec_materializes_index_install(tmp_path, monkeypatch):
    class FakeDist:
        version = "9.9.9"

        def read_text(self, name):
            return None

    monkeypatch.setattr("importlib.metadata.distribution", lambda _name: FakeDist())
    monkeypatch.setattr("plaibook.openshell_sdk._checkout_source_path", lambda: None)
    spec = plaibook_install_spec(home=tmp_path)
    root = Path(spec)
    assert root == tmp_path / ".cache" / "ansible-plaibook" / "pypi-src"
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "plaibook"' in text
    assert (root / "plaibook" / "openshell_sdk.py").is_file()
    assert "plaibook==" not in spec
    assert "pypi.org" not in spec


def test_plaibook_install_spec_materializes_pypi_direct_url(tmp_path, monkeypatch):
    class FakeDist:
        version = "9.9.9"

        def read_text(self, name):
            if name == "direct_url.json":
                return json.dumps(
                    {
                        "url": "https://files.pythonhosted.org/packages/plaibook.whl",
                        "archive_info": {"hash": "sha256:abc"},
                    }
                )
            return None

    monkeypatch.setattr("importlib.metadata.distribution", lambda _name: FakeDist())
    monkeypatch.setattr("plaibook.openshell_sdk._checkout_source_path", lambda: None)
    spec = plaibook_install_spec(home=tmp_path)
    assert Path(spec).joinpath("pyproject.toml").is_file()
    assert "files.pythonhosted.org" not in spec
    assert "plaibook==" not in spec


def test_spec_from_direct_url_rejects_http_git():
    with pytest.raises(OpenshellSdkError, match="HTTP"):
        spec_from_direct_url(
            {
                "url": "http://github.com/aknochow/ansible-plaibook.git",
                "vcs_info": {"vcs": "git", "commit_id": "abc123"},
            },
            "0.1.0",
        )


def test_find_sdk_python_prefers_311(monkeypatch, tmp_path):
    py311 = tmp_path / "python3.11"
    py312 = tmp_path / "python3.12"
    py311.write_text("")
    py312.write_text("")

    def which(name):
        if name == "python3.11":
            return str(py311)
        if name == "python3.12":
            return str(py312)
        return None

    monkeypatch.setattr("plaibook.openshell_sdk.shutil.which", which)
    monkeypatch.setattr("plaibook.openshell_sdk._BREW_BIN", ())
    monkeypatch.setattr("plaibook.openshell_sdk._BREW_OPT", ())
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)
    assert find_sdk_python() == str(py311)


def test_find_sdk_python_uses_homebrew_when_path_misses(monkeypatch, tmp_path):
    brew = tmp_path / "python3.11"
    brew.write_text("")
    monkeypatch.setattr("plaibook.openshell_sdk.shutil.which", lambda _name: None)
    monkeypatch.setattr("plaibook.openshell_sdk._BREW_BIN", (tmp_path,))
    monkeypatch.setattr("plaibook.openshell_sdk._BREW_OPT", ())
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)
    assert find_sdk_python() == str(brew)


def test_find_sdk_python_uses_homebrew_opt_when_bin_misses(monkeypatch, tmp_path):
    brew = tmp_path / "python@3.11" / "bin" / "python3.11"
    brew.parent.mkdir(parents=True)
    brew.write_text("")
    monkeypatch.setattr("plaibook.openshell_sdk.shutil.which", lambda _name: None)
    monkeypatch.setattr("plaibook.openshell_sdk._BREW_BIN", ())
    monkeypatch.setattr("plaibook.openshell_sdk._BREW_OPT", (tmp_path,))
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)
    assert find_sdk_python() == str(brew)


def test_prepare_runtime_creates_venv_and_installs(monkeypatch, tmp_path):
    recorded = []
    base = tmp_path / "pythons" / "python3.11"
    base.parent.mkdir()
    base.write_text("")

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        if cmd[1:3] == ["-m", "venv"]:
            bindir = Path(cmd[-1]) / "bin"
            bindir.mkdir(parents=True)
            (bindir / "python").write_text("")
            (bindir / "plai").write_text("")
        if len(cmd) > 3 and cmd[2] == "pip" and cmd[3] == "wheel":
            out = Path(cmd[cmd.index("-w") + 1])
            (out / "plaibook-0.1.7-py3-none-any.whl").write_bytes(b"wheel")
        return SimpleNamespace(returncode=0, stdout="3.11.11\n", stderr="")

    ensured = []
    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: str(base))
    monkeypatch.setattr("plaibook.openshell_sdk.plaibook_install_spec", lambda **_k: "git+https://example/plaibook.git@abc")
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_version", lambda _exe: (3, 11, 11))
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    monkeypatch.setattr(
        "plaibook.openshell_sdk.ensure_openshell_sdk",
        lambda python, **kwargs: ensured.append(python),
    )
    python = prepare_sandbox_runtime(stderr=None, home=tmp_path)
    runtime = tmp_path / ".cache" / "ansible-plaibook" / "sandbox-runtime"
    assert python == str(runtime / "bin" / "python")
    assert recorded[0][:3] == [str(base), "-m", "venv"]
    assert recorded[1][1:4] == ["-m", "pip", "install"]
    assert "--require-hashes" in recorded[1]
    assert recorded[1][-1] == str(hashed_requirements("build-backend-requirements.txt"))
    assert recorded[2][1:4] == ["-m", "pip", "wheel"]
    assert "--no-deps" in recorded[2]
    assert "--no-build-isolation" in recorded[2]
    assert recorded[2][0] == python
    assert recorded[2][-1] == "git+https://example/plaibook.git@abc"
    assert recorded[3][1:4] == ["-m", "pip", "install"]
    assert "--no-deps" in recorded[3]
    assert "--require-hashes" in recorded[3]
    assert SDK_SPEC not in recorded[3]
    assert recorded[4][1:4] == ["-m", "pip", "install"]
    assert "--require-hashes" in recorded[4]
    assert recorded[4][-1] == str(hashed_requirements(RUNTIME_HASHED_REQUIREMENTS))
    assert recorded[5][1:4] == ["-m", "pip", "install"]
    assert "--require-hashes" in recorded[5]
    assert recorded[5][-1] == str(hashed_requirements(HASHED_REQUIREMENTS))
    assert ensured == [python]
    stamp = json.loads((tmp_path / ".cache" / "ansible-plaibook" / "sandbox-runtime.json").read_text())
    assert stamp["spec"] == "git+https://example/plaibook.git@abc"
    assert stamp["locks"]
    assert stamp["source"] == ""


def test_prepare_runtime_reuses_matching_stamp(monkeypatch, tmp_path):
    cache = tmp_path / ".cache" / "ansible-plaibook"
    runtime = cache / "sandbox-runtime"
    bindir = runtime / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python").write_text("")
    (bindir / "plai").write_text("")
    base = tmp_path / "python3.11"
    base.write_text("")
    spec = "git+https://example/plaibook.git@abc"
    (cache / "sandbox-runtime.json").write_text(
        json.dumps(
            {
                "base": str(base.resolve()),
                "base_version": "3.11.11",
                "spec": spec,
                "locks": "lock-id",
            }
        )
        + "\n"
    )
    calls = []
    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: str(base))
    monkeypatch.setattr("plaibook.openshell_sdk.plaibook_install_spec", lambda **_k: spec)
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_version", lambda _exe: (3, 11, 11))
    monkeypatch.setattr("plaibook.openshell_sdk._runtime_lock_id", lambda: "lock-id")
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr("plaibook.openshell_sdk.ensure_openshell_sdk", lambda python, **kwargs: None)
    python = prepare_sandbox_runtime(stderr=None, home=tmp_path)
    assert python == str(bindir / "python")
    assert calls == []


def test_prepare_runtime_rebuilds_when_local_source_changes(monkeypatch, tmp_path):
    checkout = tmp_path / "src"
    checkout.mkdir()
    (checkout / "mod.py").write_text("n = 1\n")
    spec = str(checkout)
    cache = tmp_path / ".cache" / "ansible-plaibook"
    runtime = cache / "sandbox-runtime"
    bindir = runtime / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python").write_text("")
    (bindir / "plai").write_text("")
    base = tmp_path / "python3.11"
    base.write_text("")

    (cache / "sandbox-runtime.json").write_text(
        json.dumps(
            {
                "base": str(base.resolve()),
                "base_version": "3.11.11",
                "spec": spec,
                "locks": "lock-id",
                "source": _source_fingerprint(spec),
            }
        )
        + "\n"
    )
    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        if cmd[1:3] == ["-m", "venv"]:
            new_bindir = Path(cmd[-1]) / "bin"
            new_bindir.mkdir(parents=True)
            (new_bindir / "python").write_text("")
            (new_bindir / "plai").write_text("")
        if len(cmd) > 3 and cmd[2] == "pip" and cmd[3] == "wheel":
            out = Path(cmd[cmd.index("-w") + 1])
            (out / "plaibook-0.1.7-py3-none-any.whl").write_bytes(b"wheel")
        return SimpleNamespace(returncode=0, stdout="3.11.11\n", stderr="")

    (checkout / "mod.py").write_text("n = 2\n")
    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: str(base))
    monkeypatch.setattr("plaibook.openshell_sdk.plaibook_install_spec", lambda **_k: spec)
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_version", lambda _exe: (3, 11, 11))
    monkeypatch.setattr("plaibook.openshell_sdk._runtime_lock_id", lambda: "lock-id")
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    monkeypatch.setattr("plaibook.openshell_sdk.ensure_openshell_sdk", lambda python, **kwargs: None)
    python = prepare_sandbox_runtime(stderr=None, home=tmp_path)
    assert python == str(runtime / "bin" / "python")
    assert recorded[0][:3] == [str(base), "-m", "venv"]
    stamp = json.loads((cache / "sandbox-runtime.json").read_text())
    assert stamp["source"] == _source_fingerprint(spec)


def test_prepare_runtime_rebuilds_when_hashed_locks_change(monkeypatch, tmp_path):
    cache = tmp_path / ".cache" / "ansible-plaibook"
    runtime = cache / "sandbox-runtime"
    bindir = runtime / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python").write_text("")
    (bindir / "plai").write_text("")
    base = tmp_path / "python3.11"
    base.write_text("")
    spec = "git+https://example/plaibook.git@abc"
    (cache / "sandbox-runtime.json").write_text(
        json.dumps(
            {
                "base": str(base.resolve()),
                "base_version": "3.11.11",
                "spec": spec,
                "locks": "old-lock",
            }
        )
        + "\n"
    )
    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        if cmd[1:3] == ["-m", "venv"]:
            new_bindir = Path(cmd[-1]) / "bin"
            new_bindir.mkdir(parents=True)
            (new_bindir / "python").write_text("")
            (new_bindir / "plai").write_text("")
        if len(cmd) > 3 and cmd[2] == "pip" and cmd[3] == "wheel":
            out = Path(cmd[cmd.index("-w") + 1])
            (out / "plaibook-0.1.7-py3-none-any.whl").write_bytes(b"wheel")
        return SimpleNamespace(returncode=0, stdout="3.11.11\n", stderr="")

    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: str(base))
    monkeypatch.setattr("plaibook.openshell_sdk.plaibook_install_spec", lambda **_k: spec)
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_version", lambda _exe: (3, 11, 11))
    monkeypatch.setattr("plaibook.openshell_sdk._runtime_lock_id", lambda: "new-lock")
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    monkeypatch.setattr("plaibook.openshell_sdk.ensure_openshell_sdk", lambda python, **kwargs: None)
    python = prepare_sandbox_runtime(stderr=None, home=tmp_path)
    assert python == str(runtime / "bin" / "python")
    assert recorded[0][:3] == [str(base), "-m", "venv"]
    assert "--no-build-isolation" in recorded[2]
    stamp = json.loads((cache / "sandbox-runtime.json").read_text())
    assert stamp["locks"] == "new-lock"


def test_prepare_runtime_holds_flock_during_create(monkeypatch, tmp_path):
    import subprocess
    import sys

    recorded_lock = []
    base = tmp_path / "pythons" / "python3.11"
    base.parent.mkdir()
    base.write_text("")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[1:3] == ["-m", "venv"]:
            lock_path = tmp_path / ".cache" / "ansible-plaibook" / LOCK_NAME
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
            recorded_lock.append(probe.returncode)
            bindir = Path(cmd[-1]) / "bin"
            bindir.mkdir(parents=True)
            (bindir / "python").write_text("")
            (bindir / "plai").write_text("")
        if len(cmd) > 3 and cmd[2] == "pip" and cmd[3] == "wheel":
            out = Path(cmd[cmd.index("-w") + 1])
            (out / "plaibook-0.1.7-py3-none-any.whl").write_bytes(b"wheel")
        return SimpleNamespace(returncode=0, stdout="3.11.11\n", stderr="")

    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: str(base))
    monkeypatch.setattr("plaibook.openshell_sdk.plaibook_install_spec", lambda **_k: "git+https://example/plaibook.git@abc")
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_version", lambda _exe: (3, 11, 11))
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    monkeypatch.setattr("plaibook.openshell_sdk.ensure_openshell_sdk", lambda python, **kwargs: None)
    prepare_sandbox_runtime(stderr=None, home=tmp_path)
    assert recorded_lock == [2]


def test_prepare_runtime_refuses_symlink(monkeypatch, tmp_path):
    cache = tmp_path / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)
    (cache / "sandbox-runtime").symlink_to(tmp_path)
    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: "/usr/bin/python3.11")
    monkeypatch.setattr("plaibook.openshell_sdk.plaibook_install_spec", lambda **_k: "/tmp/plaibook")
    with pytest.raises(OpenshellSdkError, match="symlink"):
        prepare_sandbox_runtime(stderr=None, home=tmp_path)


def test_prepare_runtime_errors_when_no_python311(monkeypatch, tmp_path):
    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: None)
    with pytest.raises(OpenshellSdkError, match="3.11"):
        prepare_sandbox_runtime(stderr=None, home=tmp_path)


def test_reexec_stays_on_capable_interpreter(monkeypatch):
    called = []
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)
    monkeypatch.setattr("plaibook.openshell_sdk.ensure_openshell_sdk", lambda **kwargs: called.append(True))

    def fail_exec(*_args, **_kwargs):
        raise AssertionError("exec")

    monkeypatch.setattr("plaibook.openshell_sdk.os.execv", fail_exec)
    reexec_sandbox_runtime(stderr=None)
    assert called == [True]


def test_reexec_switches_to_runtime_plai(monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    python = bindir / "python"
    plai = bindir / "plai"
    python.write_text("")
    plai.write_text("")
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: False)
    monkeypatch.setattr("plaibook.openshell_sdk.prepare_sandbox_runtime", lambda **kwargs: str(python))
    monkeypatch.setenv("PYTHONPATH", "/opt/homebrew/lib/python3.10/site-packages")
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/other-venv")
    execed = {}

    def fake_exec(path, argv):
        execed["path"] = path
        execed["argv"] = list(argv)

    monkeypatch.setattr("plaibook.openshell_sdk.os.execv", fake_exec)
    monkeypatch.setattr("plaibook.openshell_sdk.sys.argv", ["plai", "review", "org/repo/1"])
    reexec_sandbox_runtime(stderr=None)
    assert execed["path"] == str(plai)
    assert execed["argv"] == [str(plai), "review", "org/repo/1"]
    assert "PYTHONPATH" not in os.environ
    assert os.environ["VIRTUAL_ENV"] == str(tmp_path.resolve())


def test_reexec_refuses_a_second_switch(monkeypatch):
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: False)
    monkeypatch.setenv("PLAIBOOK_SANDBOX_RUNTIME", "1")

    def fail_prepare(**_kwargs):
        raise AssertionError("prepare")

    monkeypatch.setattr("plaibook.openshell_sdk.prepare_sandbox_runtime", fail_prepare)
    with pytest.raises(OpenshellSdkError, match="twice"):
        reexec_sandbox_runtime(stderr=None)


def test_sdk_satisfies_import_error_is_false(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openshell" or name.startswith("openshell."):
            raise ImportError("missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert sdk_satisfies() is False


def test_sdk_satisfies_package_not_found_is_false(monkeypatch):
    import sys
    from importlib.metadata import PackageNotFoundError
    from types import ModuleType

    fake = ModuleType("openshell")
    fake.SandboxClient = object
    monkeypatch.setitem(sys.modules, "openshell", fake)

    def boom(_name):
        raise PackageNotFoundError("openshell")

    monkeypatch.setattr("importlib.metadata.version", boom)
    assert sdk_satisfies() is False


def _protobuf_version_error(message: str) -> BaseException:
    try:
        from google.protobuf.runtime_version import VersionError
    except ImportError:
        class VersionError(Exception):
            pass

        VersionError.__module__ = "google.protobuf.runtime_version"
    return VersionError(message)


def test_sdk_satisfies_protobuf_version_error_is_a_message(monkeypatch):
    import builtins

    real_import = builtins.__import__
    mismatch = (
        "Detected incompatible Protobuf Gencode/Runtime versions when loading "
        "datamodel.proto: gencode 6.31.1 runtime 5.29.6. Runtime version cannot be older "
        "than the linked gencode version."
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openshell" or name.startswith("openshell."):
            raise _protobuf_version_error(mismatch)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(OpenshellSdkError) as caught:
        sdk_satisfies()
    message = str(caught.value)
    assert "protobuf" in message
    assert "6.31.1" in message
    assert "5.29.6" in message
    assert "Upgrade protobuf" in message
    assert "Traceback" not in message


def test_ensure_openshell_sdk_does_not_reinstall_on_protobuf_version_error(monkeypatch):
    import builtins

    real_import = builtins.__import__
    mismatch = (
        "Detected incompatible Protobuf Gencode/Runtime versions when loading "
        "datamodel.proto: gencode 6.31.1 runtime 5.29.6."
    )

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openshell" or name.startswith("openshell."):
            raise _protobuf_version_error(mismatch)
        return real_import(name, globals, locals, fromlist, level)

    def fail_run(*_args, **_kwargs):
        raise AssertionError("pip should not run")

    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)
    monkeypatch.setattr("builtins.__import__", fake_import)
    monkeypatch.setattr("plaibook.openshell_sdk._run", fail_run)
    with pytest.raises(OpenshellSdkError, match="Upgrade protobuf"):
        ensure_openshell_sdk()


def test_sdk_satisfies_unexpected_error_propagates(monkeypatch):
    import sys
    from types import ModuleType

    fake = ModuleType("openshell")
    fake.SandboxClient = object
    monkeypatch.setitem(sys.modules, "openshell", fake)

    def boom(_name):
        raise RuntimeError("metadata exploded")

    monkeypatch.setattr("importlib.metadata.version", boom)
    with pytest.raises(RuntimeError, match="metadata exploded"):
        sdk_satisfies()


def _fake_installed_sdk(monkeypatch, installed: str) -> None:
    import sys
    from types import ModuleType

    fake = ModuleType("openshell")
    fake.SandboxClient = object
    monkeypatch.setitem(sys.modules, "openshell", fake)
    monkeypatch.setattr("importlib.metadata.version", lambda _name: installed)


def test_pinned_sdk_version_reads_the_hashed_file():
    from plaibook.openshell_sdk import pinned_sdk_version
    from plaibook.pip_hashed import pinned_versions

    assert pinned_sdk_version() == tuple(int(x) for x in pinned_versions(HASHED_REQUIREMENTS)["openshell"].split("."))


def test_sdk_older_than_the_pin_is_upgraded(monkeypatch):
    monkeypatch.setattr("plaibook.openshell_sdk.pinned_sdk_version", lambda: (0, 1, 6))
    _fake_installed_sdk(monkeypatch, "0.1.4")
    assert sdk_satisfies() is False


def test_sdk_at_or_above_the_pin_is_kept(monkeypatch):
    monkeypatch.setattr("plaibook.openshell_sdk.pinned_sdk_version", lambda: (0, 1, 6))
    for installed in ("0.1.6", "0.1.7"):
        _fake_installed_sdk(monkeypatch, installed)
        assert sdk_satisfies() is True


def test_sdk_outside_the_range_is_refused_even_without_a_pin(monkeypatch):
    monkeypatch.setattr("plaibook.openshell_sdk.pinned_sdk_version", lambda: None)
    _fake_installed_sdk(monkeypatch, "0.2.0")
    assert sdk_satisfies() is False
    _fake_installed_sdk(monkeypatch, "0.1.4")
    assert sdk_satisfies() is True


def test_externally_managed_keeps_an_older_in_range_sdk(monkeypatch, capsys):
    import io

    from plaibook import openshell_sdk as sdk

    monkeypatch.setattr(sdk, "interpreter_supports_sdk", lambda _exe: True)
    monkeypatch.setattr(sdk, "_interpreter_satisfies", lambda _exe: False)
    monkeypatch.setattr(sdk, "interpreter_is_externally_managed", lambda _exe: True)
    monkeypatch.setattr(sdk, "_installed_version", lambda _exe: "0.1.4")
    err = io.StringIO()
    sdk.ensure_openshell_sdk("/usr/bin/python3", stderr=err)
    assert "0.1.4" in err.getvalue() and "kept" in err.getvalue()


def test_externally_managed_without_an_sdk_still_refuses(monkeypatch):
    from plaibook import openshell_sdk as sdk

    monkeypatch.setattr(sdk, "interpreter_supports_sdk", lambda _exe: True)
    monkeypatch.setattr(sdk, "_interpreter_satisfies", lambda _exe: False)
    monkeypatch.setattr(sdk, "interpreter_is_externally_managed", lambda _exe: True)
    monkeypatch.setattr(sdk, "_installed_version", lambda _exe: None)
    with pytest.raises(sdk.OpenshellSdkError, match="not installed"):
        sdk.ensure_openshell_sdk("/usr/bin/python3")
