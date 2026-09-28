# -*- coding: utf-8 -*-
"""The review workflow stays least-privilege and does not float a secret ref."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    text = (ROOT / ".github" / "workflows" / name).read_text()
    return yaml.safe_load(text)


def test_reusable_workflow_permissions_and_pins():
    doc = _load("plai-review-reusable.yml")
    assert doc["permissions"] == {}
    job = doc["jobs"]["launch"]
    assert job["permissions"]["pull-requests"] == "write"
    assert job["permissions"]["checks"] == "write"
    assert "contents" in job["permissions"]
    assert job["permissions"]["contents"] == "read"
    text = (ROOT / ".github" / "workflows" / "plai-review-reusable.yml").read_text()
    uses = re.findall(r"(?m)^\s*uses:\s*(\S+)", text)
    assert uses
    for ref in uses:
        sha = ref.rsplit("@", 1)[-1]
        assert re.fullmatch(r"[0-9a-f]{40}", sha), ref
    assert "PLAIBOOK_DISPATCH_TOKEN" not in text
    assert "permissions: write-all" not in text


def test_caller_names_the_check_command_and_does_not_use_main():
    text = (ROOT / ".github" / "workflows" / "plai-review.yml").read_text()
    doc = yaml.safe_load(text)
    assert doc["permissions"] == {}
    assert doc["jobs"]["launch"]["uses"].startswith("./")
    assert "@main" not in text
    assert "/plai-review" in text
    assert "AAP_CONTROLLER_TOKEN" in text
    assert "PLAIBOOK_DISPATCH_TOKEN" not in text
