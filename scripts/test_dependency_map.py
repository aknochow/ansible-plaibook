# -*- coding: utf-8 -*-
"""The dependency map names the pins the repositories actually record."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "docs" / "dependency-map.md"
REQUIREMENTS = ROOT / "collections-requirements.yml"
HASHED = ROOT / "plaibook" / "hashed"
RUN = ROOT / ".github" / "workflows" / "plai-review-run.yml"
CALLER = ROOT / ".github" / "workflows" / "plai-review.yml"
_SHA = re.compile(r"\b[0-9a-f]{40}\b")
_DIRECT = (
    "anthropic",
    "claude-agent-sdk",
    "cursor-sdk",
    "google-genai",
    "openai",
    "openshell",
)


def _hashed_pin(name: str) -> str:
    prefix = f"{name}=="
    found = set()
    for path in sorted(HASHED.glob("*-requirements.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().rstrip("\\").strip()
            if stripped.startswith(prefix):
                found.add(stripped)
    assert found, name
    assert len(found) == 1, found
    return found.pop()


def test_dependency_map_names_every_collection_sha():
    text = MAP.read_text(encoding="utf-8")
    document = yaml.safe_load(REQUIREMENTS.read_text(encoding="utf-8"))
    shas = [item["version"].split()[0] for item in document["collections"]]
    assert shas
    assert all(_SHA.fullmatch(sha) for sha in shas)
    missing = [sha for sha in shas if sha not in text]
    assert missing == []


def test_dependency_map_names_hashed_sdk_pins():
    text = MAP.read_text(encoding="utf-8")
    missing = [pin for name in _DIRECT if (pin := _hashed_pin(name)) not in text]
    assert missing == []


def test_dependency_map_names_review_workflow_pins():
    text = MAP.read_text(encoding="utf-8")
    run = RUN.read_text(encoding="utf-8")
    caller = CALLER.read_text(encoding="utf-8")
    checkout_refs = set(re.findall(r'ref:\s*"([0-9a-f]{40})"', run))
    assert len(checkout_refs) == 1
    caller_refs = set(re.findall(r"plai-review-run\.yml@([0-9a-f]{40})", caller))
    assert len(caller_refs) == 1
    gateway = re.search(r"OPENSHELL_VERSION=(v\d+\.\d+\.\d+)", run)
    installer = re.search(r"NVIDIA/OpenShell/([0-9a-f]{40})/install\.sh", run)
    image = re.search(r"SANDBOX_IMAGE:\s*(\S+)", run)
    assert gateway and installer and image
    required = [
        checkout_refs.pop(),
        caller_refs.pop(),
        gateway.group(1),
        installer.group(1),
        image.group(1),
    ]
    missing = [pin for pin in required if pin not in text]
    assert missing == []


def test_entry_points_link_the_dependency_map():
    expected = {
        "README.md": "docs/dependency-map.md",
        "AGENTS.md": "docs/dependency-map.md",
        "CONTRIBUTING.md": "docs/dependency-map.md",
        "docs/index.md": "dependency-map.md",
    }
    for relative, needle in expected.items():
        assert needle in (ROOT / relative).read_text(encoding="utf-8"), relative
