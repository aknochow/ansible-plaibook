# -*- coding: utf-8 -*-
"""Resolve the OpenShell gateway URL from the CLI's on-disk config.

The selected gateway name defaults to ``openshell``. Its endpoint lives
in ``~/.config/openshell/gateways/<name>/metadata.json``. When that file
is absent, the playbook keeps its fallback URL.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_GATEWAY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_DEFAULT_GATEWAY_NAME = "openshell"


def openshell_gateway_endpoint(name: str, fallback: str, config_home: str | None = None) -> str:
    """Return ``gateway_endpoint`` for *name*, or *fallback* when unset."""
    gateway = str(name or "").strip() or _DEFAULT_GATEWAY_NAME
    if _GATEWAY_NAME.fullmatch(gateway) is None:
        return fallback
    home = config_home if config_home is not None else os.environ.get("HOME", "")
    meta = Path(home) / ".config" / "openshell" / "gateways" / gateway / "metadata.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return fallback
    if not isinstance(data, dict):
        return fallback
    endpoint = data.get("gateway_endpoint")
    if not isinstance(endpoint, str):
        return fallback
    endpoint = endpoint.strip()
    if not endpoint.startswith("https://") or any(ch.isspace() for ch in endpoint):
        return fallback
    if "@" in endpoint.split("://", 1)[-1].split("/", 1)[0]:
        return fallback
    return endpoint


def openshell_ssh_proxy(
    configured: str,
    home: str | None = None,
    collection_paths: list[str] | str | None = None,
) -> str:
    """Return the ssh_proxy.py the review should run.

    An explicit *configured* path that exists wins. Otherwise use the
    aknochow.openshell collection plaibook installed, then the first copy on
    Ansible's collection search path (the execution environment's). A
    developer checkout is used only when it is configured explicitly.
    """
    chosen = str(configured or "").strip()
    if chosen and Path(chosen).is_file():
        return chosen
    root = Path(home) if home else Path.home()
    roots = [root / ".cache" / "ansible-plaibook" / "collections"]
    if isinstance(collection_paths, str):
        collection_paths = collection_paths.split(os.pathsep)
    for entry in collection_paths or []:
        if str(entry).strip():
            roots.append(Path(str(entry)).expanduser())
    for base in roots:
        for candidate in (
            base / "ansible_collections" / "aknochow" / "openshell" / "scripts" / "ssh_proxy.py",
            base / "aknochow" / "openshell" / "scripts" / "ssh_proxy.py",
        ):
            if candidate.is_file():
                return str(candidate)
    return chosen


class FilterModule:
    def filters(self):
        return {
            "openshell_gateway_endpoint": openshell_gateway_endpoint,
            "openshell_ssh_proxy": openshell_ssh_proxy,
        }
