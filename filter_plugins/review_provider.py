# -*- coding: utf-8 -*-
"""Name the collection and effort a review ran with, for run results."""

from __future__ import annotations

import re

import yaml

_COLLECTION_BY_FAMILY = {
    "claude": "claude",
    "claude_cli": "claude",
    "cursor": "cursor",
    "gemini": "gemini",
    "openai": "openai",
    "xai": "xai",
}
_PIN = re.compile(r"[0-9a-f]{7,40}")


def review_collection_ref(family: str, requirements_path: str) -> str:
    """Return ``aknochow.<name>@<short sha>``, or an empty string when unknown.

    Only display depends on this, so a missing or malformed pin file must
    not fail the write of a finished review's results.
    """
    name = _COLLECTION_BY_FAMILY.get(str(family))
    if name is None:
        return ""
    try:
        with open(requirements_path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        rows = data["collections"]
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        return ""
    if not isinstance(rows, list):
        return ""
    url = f"https://github.com/aknochow/ansible-{name}.git"
    for row in rows:
        if isinstance(row, dict) and row.get("name") == url:
            version = str(row.get("version") or "")
            return f"aknochow.{name}@{version[:7]}" if _PIN.fullmatch(version) else ""
    return ""


def review_effort_label(lens: str, tools: str) -> str:
    """Describe the effort sent to lens calls and to explore and verify calls."""
    if lens == tools:
        return lens or ""
    return f"lens {lens or 'default'}, tools {tools or 'default'}"


class FilterModule:
    def filters(self):
        return {"review_collection_ref": review_collection_ref, "review_effort_label": review_effort_label}
