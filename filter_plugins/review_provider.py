# -*- coding: utf-8 -*-
"""Name the collection a review family runs on, for run results."""

from __future__ import annotations

import yaml

_COLLECTION_BY_FAMILY = {
    "claude": "claude",
    "claude_cli": "claude",
    "cursor": "cursor",
    "gemini": "gemini",
    "openai": "openai",
    "xai": "xai",
}


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
            return f"aknochow.{name}@{str(row.get('version') or '')[:7]}".rstrip("@")
    return ""


class FilterModule:
    def filters(self):
        return {"review_collection_ref": review_collection_ref}
