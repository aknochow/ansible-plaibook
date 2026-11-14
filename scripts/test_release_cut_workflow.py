# -*- coding: utf-8 -*-
"""A release is tagged only after the execution environment builds and passes its scan."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(name: str) -> dict:
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as True.
    document["on"] = document.pop(True, document.get("on"))
    return document


def test_ee_workflow_can_be_called():
    assert "workflow_call" in _load("ee.yml")["on"]


def test_ee_workflow_gates_on_fixable_high_and_critical():
    steps = _load("ee.yml")["jobs"]["build"]["steps"]
    gates = [s for s in steps if s.get("with", {}).get("exit-code") == "1"]
    assert len(gates) == 1
    assert gates[0]["with"]["severity"] == "HIGH,CRITICAL"
    assert gates[0]["with"]["ignore-unfixed"] == "true"


def test_release_cut_runs_the_ee_before_the_tag():
    jobs = _load("release-cut.yml")["jobs"]
    ee = jobs["ee"]
    assert ee["uses"] == "./.github/workflows/ee.yml"
    assert ee["needs"] == "check"
    assert ee["if"] == "needs.check.outputs.release == 'true'"
    assert ee["permissions"] == {"contents": "read"}
    assert "secrets" not in ee
    cut = jobs["cut"]
    assert set(cut["needs"]) == {"check", "ee"}
    # No status function in `if`, so a failed or skipped ee job skips the cut.
    assert "always()" not in cut["if"]
    assert "failure()" not in cut["if"]
