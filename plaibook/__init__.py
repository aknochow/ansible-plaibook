# -*- coding: utf-8 -*-
"""plaibook CLI package. Wraps a bundled (or checkout) review.yml; not a Galaxy collection."""

from __future__ import annotations

import json
import re
from importlib.metadata import PackageNotFoundError, distribution

__all__ = ["__version__", "version_text"]

__version__ = "0.1.26"

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


def version_text(direct_url: dict | None | object = ...) -> str:
    """Version line for ``plai --version``.

    A PyPI install is ``plaibook 0.1.26``. A git install adds ``dev`` and
    the requested revision plus a short commit, for example
    ``plaibook 0.1.26 (dev, main@664b8e1d39ac)``. A tag named ``v`` plus
    the package version omits ``dev``.
    """
    data = _direct_url() if direct_url is ... else direct_url
    base = f"plaibook {__version__}"
    if not isinstance(data, dict):
        return base
    vcs = data.get("vcs_info")
    if not isinstance(vcs, dict) or vcs.get("vcs") != "git":
        return base
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
