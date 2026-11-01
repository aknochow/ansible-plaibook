# -*- coding: utf-8 -*-
"""plaibook CLI package. Wraps a bundled (or checkout) review.yml; not a Galaxy collection."""

from __future__ import annotations

import json
import os
import re
import subprocess
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["__version__", "version_text"]

__version__ = "0.1.30"

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_REVISION = re.compile(r"^[A-Za-z0-9._/-]{1,80}$")


def _direct_url() -> dict | None:
    """PEP 610 record for this install, or None for a normal PyPI install."""
    try:
        dist = distribution("plaibook")
    except PackageNotFoundError:
        return None
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _is_plaibook_checkout(root: Path) -> bool:
    pyproject = root / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return 'name = "plaibook"' in text


def _package_checkout_root() -> Path | None:
    root = Path(__file__).resolve().parent.parent
    if _is_plaibook_checkout(root):
        return root
    return None


def _checkout_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("PLAIBOOK_ROOT", "GITHUB_WORKSPACE"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            roots.append(Path(raw))
    package_root = _package_checkout_root()
    if package_root is not None:
        roots.append(package_root)
    seen: set[Path] = set()
    ordered: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def _git_output(root: Path, *args: str) -> str | None:
    if not (root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    text = (completed.stdout or "").strip()
    return text or None


_INDEX_HOSTS = frozenset({"files.pythonhosted.org", "pypi.org"})


def _index_install(data: dict) -> bool:
    """A wheel or sdist from an index. Those stay on the package version.

    The host is the URL hostname. ``files.pythonhosted.org`` or ``pypi.org``
    anywhere else in the URL is not an index install.
    """
    try:
        host = urlsplit(str(data.get("url") or "")).hostname
    except ValueError:
        return False
    return bool(host) and host.lower() in _INDEX_HOSTS


def _vcs_info_from_checkout() -> dict[str, str] | None:
    for root in _checkout_roots():
        if not _is_plaibook_checkout(root):
            continue
        commit = _git_output(root, "rev-parse", "HEAD")
        if not commit or not _COMMIT.fullmatch(commit):
            continue
        revision = _git_output(root, "symbolic-ref", "-q", "--short", "HEAD")
        if revision and _REVISION.fullmatch(revision):
            return {"commit_id": commit, "requested_revision": revision}
        # Detached HEAD has no branch. An exact v<version> tag is a release
        # checkout; anything else stays on the short commit.
        tag = _git_output(root, "describe", "--tags", "--exact-match", "HEAD")
        if tag == f"v{__version__}" and _REVISION.fullmatch(tag or ""):
            return {"commit_id": commit, "requested_revision": tag}
        return {"commit_id": commit, "requested_revision": commit}
    return None


def _format_git_version(base: str, vcs: dict[str, str]) -> str:
    commit = str(vcs.get("commit_id") or "")
    if not _COMMIT.fullmatch(commit):
        return base
    short = commit[:12]
    revision = str(vcs.get("requested_revision") or "")
    if not _REVISION.fullmatch(revision) or revision == commit:
        return f"{base} (dev, {short})"
    if revision == f"v{__version__}":
        return f"{base} ({revision}@{short})"
    return f"{base} (dev, {revision}@{short})"


def _format_from_direct_url(base: str, data: dict) -> str | None:
    vcs = data.get("vcs_info")
    if not isinstance(vcs, dict) or vcs.get("vcs") != "git":
        return None
    commit = str(vcs.get("commit_id") or "")
    if not _COMMIT.fullmatch(commit):
        return None
    revision = str(vcs.get("requested_revision") or "")
    payload = {"commit_id": commit}
    if revision:
        payload["requested_revision"] = revision
    return _format_git_version(base, payload)


def version_text(direct_url: dict | None | object = ...) -> str:
    """Version line for ``plai --version``.

    A PyPI install is ``plaibook 0.1.26``. A git install adds ``dev`` and
    the requested revision plus a short commit, for example
    ``plaibook 0.1.26 (dev, main@664b8e1d39ac)``. A tag named ``v`` plus
    the package version omits ``dev``.

    When PEP 610 ``direct_url.json`` is missing (for example ``uv sync`` on a
    checked-out tree in CI), the line falls back to ``git rev-parse`` on
    ``PLAIBOOK_ROOT``, ``GITHUB_WORKSPACE``, or the installed package's
    source tree.
    """
    base = f"plaibook {__version__}"
    if direct_url is ...:
        data = _direct_url()
        allow_checkout_fallback = True
    else:
        data = direct_url
        allow_checkout_fallback = False
    if isinstance(data, dict):
        formatted = _format_from_direct_url(base, data)
        if formatted is not None:
            return formatted
        if _index_install(data):
            return base
    if allow_checkout_fallback:
        checkout = _vcs_info_from_checkout()
        if checkout is not None:
            return _format_git_version(base, checkout)
    return base
