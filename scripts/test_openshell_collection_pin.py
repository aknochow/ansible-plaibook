# -*- coding: utf-8 -*-
"""The pinned ansible-openshell commit still calls the hashed OpenShell SDK.

OpenShell's Python API moves often. This checks the collection SHA in
collections-requirements.yml, not a copy of that code in this repo.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement

from plaibook.pip_hashed import pinned_versions

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "collections-requirements.yml"
# Fetch only this reviewed commit. A pull request that changes the pin
# must change these constants too; the YAML values are never passed to git.
ALLOWED_REMOTE = "https://github.com/aknochow/ansible-openshell.git"
ALLOWED_SHA = "494420ce471eb81f88e5860ad2155e13bd3cda97"


def _openshell_pin() -> tuple[str, str]:
    import yaml

    document = yaml.safe_load(REQUIREMENTS.read_text(encoding="utf-8"))
    for item in document["collections"]:
        name = str(item.get("name") or "")
        if name.rstrip("/").endswith("ansible-openshell.git"):
            return name, str(item["version"]).split()[0]
    raise AssertionError("collections-requirements.yml has no ansible-openshell pin")


def _sdk_spec(collection: Path) -> str:
    source = (collection / "plugins" / "module_utils" / "openshell_client.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "OPENSHELL_SDK_SPEC":
                value = ast.literal_eval(node.value)
                assert isinstance(value, str)
                return value
    raise AssertionError("pinned collection does not assign OPENSHELL_SDK_SPEC")


def _require_reviewed_pin() -> None:
    url, sha = _openshell_pin()
    if url != ALLOWED_REMOTE or sha != ALLOWED_SHA:
        raise AssertionError(f"refusing to fetch {url}@{sha}; only {ALLOWED_REMOTE}@{ALLOWED_SHA} is allowed")


def _checkout(dest: Path) -> None:
    _require_reviewed_pin()
    if not ALLOWED_REMOTE.startswith("https://github.com/aknochow/"):
        raise AssertionError(f"refusing non-allowlisted remote {ALLOWED_REMOTE}")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "init", "-q", dest], check=True, env=env, timeout=30)
    subprocess.run(
        ["git", "fetch", "--depth", "1", ALLOWED_REMOTE, ALLOWED_SHA],
        cwd=dest,
        check=True,
        env=env,
        timeout=120,
    )
    subprocess.run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=dest, check=True, env=env, timeout=30)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert head.stdout.strip() == ALLOWED_SHA


@pytest.fixture(scope="module")
def pinned_collection(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("openshell-pin") / "collection"
    _checkout(dest)
    return dest


def test_pinned_collection_accepts_the_hashed_sdk(pinned_collection: Path):
    spec = Requirement(_sdk_spec(pinned_collection))
    installed = pinned_versions("openshell-requirements.txt")["openshell"]
    assert spec.specifier.contains(installed), f"{installed} not in {spec}"


def _load_collection(collection: Path) -> tuple[Callable[..., object], Callable[..., object]]:
    namespace = collection.parent / "ansible_collections" / "aknochow" / "openshell"
    if namespace.exists() or namespace.is_symlink():
        namespace.unlink()
    namespace.parent.mkdir(parents=True, exist_ok=True)
    namespace.symlink_to(collection)
    root = str(namespace.parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    for name in list(sys.modules):
        if name == "ansible_collections" or name.startswith("ansible_collections.aknochow.openshell"):
            del sys.modules[name]
    from ansible_collections.aknochow.openshell.plugins.module_utils.openshell_client import (
        exec_command,
    )

    proxy_path = collection / "scripts" / "ssh_proxy.py"
    spec = importlib.util.spec_from_file_location("pinned_openshell_ssh_proxy", proxy_path)
    assert spec is not None and spec.loader is not None
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    return exec_command, proxy.ssh_forward_messages


def test_pinned_collection_calls_the_installed_sdk(pinned_collection: Path):
    if sys.version_info < (3, 11):
        pytest.skip("openshell publishes no wheels for Python 3.10")
    from openshell import SandboxClient
    from openshell._proto import openshell_pb2

    exec_command, ssh_forward_messages = _load_collection(pinned_collection)
    signature = inspect.signature(SandboxClient.exec)
    seen: dict[str, object] = {}

    def exec_fn(*args, **kwargs):
        signature.bind(None, *args, **kwargs)
        seen["args"] = args
        seen["kwargs"] = kwargs
        return SimpleNamespace(exit_code=0, stdout="", stderr="")

    exec_fn.__signature__ = signature
    result = exec_command(
        SimpleNamespace(exec=exec_fn),
        "meek-grison",
        ["true"],
        workspace="default",
        sandbox_id="id-1",
        workdir="/work",
        env={"A": "b"},
        stdin=b"x",
        timeout_seconds=5,
    )
    assert result.exit_code == 0
    assert seen["args"] == ("meek-grison", ["true"])
    assert seen["kwargs"] == {
        "workspace": "default",
        "workdir": "/work",
        "env": {"A": "b"},
        "stdin": b"x",
        "timeout_seconds": 5,
    }

    session, init = ssh_forward_messages(openshell_pb2, "meek-grison", "id-1", "default")
    assert session.sandbox == "meek-grison"
    assert init.sandbox == "meek-grison"
    assert init.workspace == "default"
