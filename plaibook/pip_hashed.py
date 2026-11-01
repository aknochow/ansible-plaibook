# -*- coding: utf-8 -*-
"""Locate checked-in hashed requirements and build pip --require-hashes argv."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

HASHED_DIR = Path(__file__).resolve().parent / "hashed"
_DIST_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?==([^\\\s]+)")


def hashed_requirements(filename: str) -> Path:
    path = HASHED_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"hashed requirements missing: {path}")
    return path


class HashedInstallError(RuntimeError):
    """This interpreter cannot run a hashed requirements install."""


def pip_install_hashed_argv(python: str, filename: str, *, dry_run: bool = False) -> list[str]:
    argv = [python, "-m", "pip", "install", "--disable-pip-version-check"]
    if dry_run:
        argv.append("--dry-run")
    argv += ["--require-hashes", "-r", str(hashed_requirements(filename))]
    return argv


def python_has_pip(python: str) -> bool:
    """True when ``python -m pip`` can run.

    pipx environments created with the uv backend have no pip module.
    """
    probe = "import importlib.util, sys\nsys.exit(0 if importlib.util.find_spec('pip') else 1)\n"
    try:
        completed = subprocess.run(
            [python, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def uv_pip_install_hashed_argv(python: str, filename: str, *, dry_run: bool = False) -> list[str]:
    uv = shutil.which("uv")
    if not uv:
        raise HashedInstallError(
            f"{python} has no pip module, and uv is not on PATH. "
            "A pipx install that uses uv installs provider SDKs with uv pip."
        )
    argv = [uv, "pip", "install", "--no-config", "--python", python]
    if dry_run:
        argv.append("--dry-run")
    argv += ["--require-hashes", "-r", str(hashed_requirements(filename))]
    return argv


def hashed_uninstall_argv(python: str, distribution: str, *, has_pip: bool | None = None) -> list[str]:
    present = python_has_pip(python) if has_pip is None else has_pip
    if present:
        return [python, "-m", "pip", "uninstall", "-y", "--disable-pip-version-check", distribution]
    uv = shutil.which("uv")
    if not uv:
        raise HashedInstallError(
            f"{python} has no pip module, and uv is not on PATH. "
            "A pipx install that uses uv removes packages with uv pip."
        )
    return [uv, "pip", "uninstall", "--python", python, distribution]


def hashed_install_argv(
    python: str,
    filename: str,
    *,
    dry_run: bool = False,
    has_pip: bool | None = None,
) -> list[str]:
    """``python -m pip`` when pip exists, otherwise ``uv pip``.

    Does not ask the operator to install pip.
    """
    present = python_has_pip(python) if has_pip is None else has_pip
    if present:
        return pip_install_hashed_argv(python, filename, dry_run=dry_run)
    return uv_pip_install_hashed_argv(python, filename, dry_run=dry_run)


def pinned_versions(filename: str) -> dict[str, str]:
    """Map PEP 503 distribution names to the exact versions in ``filename``."""
    pins: dict[str, str] = {}
    for raw in hashed_requirements(filename).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        match = _DIST_PIN.match(line)
        if not match:
            continue
        pins[_normalize_dist(match.group(1))] = match.group(2)
    return pins


def lock_digest(*filenames: str) -> str:
    """Stable identity of hashed requirement files (stamp / cache keys)."""
    digest = hashlib.sha256()
    for name in filenames:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashed_requirements(name).read_bytes())
    return digest.hexdigest()


def _normalize_dist(name: str) -> str:
    return name.replace("_", "-").lower()
