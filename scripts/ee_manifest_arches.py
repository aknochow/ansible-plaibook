# -*- coding: utf-8 -*-
"""Fail unless an OCI index lists linux/amd64 and linux/arm64."""

from __future__ import annotations

import json
import sys
from typing import Any

REQUIRED = {("linux", "amd64"), ("linux", "arm64")}


def platforms(document: dict[str, Any]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for manifest in document.get("manifests") or []:
        if not isinstance(manifest, dict):
            continue
        platform = manifest.get("platform") or {}
        if not isinstance(platform, dict):
            continue
        system = platform.get("os")
        architecture = platform.get("architecture")
        if system and architecture:
            found.add((str(system), str(architecture)))
    return found


def missing_platforms(document: dict[str, Any]) -> list[tuple[str, str]]:
    return sorted(REQUIRED - platforms(document))


def main() -> int:
    document = json.load(sys.stdin)
    missing = missing_platforms(document)
    present = sorted(platforms(document))
    print("platforms:", ", ".join(f"{system}/{arch}" for system, arch in present) or "(none)")
    if missing:
        print(
            "missing required platforms:",
            ", ".join(f"{system}/{arch}" for system, arch in missing),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
