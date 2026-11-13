# -*- coding: utf-8 -*-
"""The Security Scan section of findings.md stays short unless the scan found something."""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "roles" / "review" / "templates" / "findings.md.j2"
OPEN = "{% if review_guardian_scan_enabled | bool %}"


def _section() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index(OPEN)
    depth = 0
    pos = start
    while True:
        nxt_if = text.find("{% if ", pos)
        nxt_end = text.find("{% endif %}", pos)
        if nxt_if != -1 and nxt_if < nxt_end:
            depth += 1
            pos = nxt_if + 1
            continue
        depth -= 1
        pos = nxt_end + len("{% endif %}")
        if depth == 0:
            return text[start:pos]


def _render(**overrides) -> str:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["bool"] = bool
    env.globals["evidence_fence"] = lambda _snippet: "```"
    values = {
        "review_guardian_scan_enabled": True,
        "review_guardian_scan_blocking_enabled": True,
        "review_guardian_blocking_rule_ids": ["SECRET-001"],
        "review_guardian_max_scan_chars": 100,
        "guardian_scan_available": True,
        "guardian_scan_skip_reason": "",
        "guardian_findings": [],
        "guardian_blocking_findings": [],
    }
    values.update(overrides)
    return env.from_string(_section()).render(**values)


def _body(rendered: str) -> list[str]:
    lines = [line for line in rendered.splitlines() if line.strip()]
    assert lines[0] == "### Security Scan (ai-guardian)"
    return lines[1:]


def test_skipped_scan_is_one_line():
    body = _body(_render(guardian_scan_available=False, guardian_scan_skip_reason="ai-guardian not installed"))
    assert body == ["Not scanned this run (ai-guardian not installed). This is not a clean result."]


def test_clean_scan_is_one_line():
    assert _body(_render()) == ["No findings: clean scan."]


@pytest.mark.parametrize("available", [False, True])
def test_short_sections_carry_no_blocking_essay(available):
    rendered = _render(guardian_scan_available=available, guardian_scan_skip_reason="ai-guardian install failed")
    assert "BLOCKING" not in rendered
    assert "\U0001f512" not in rendered
    assert "deterministic" not in rendered


def test_findings_keep_the_blocking_explanation():
    finding = {"level": "high", "rule_id": "SECRET-001", "message": "token"}
    rendered = _render(guardian_findings=[finding], guardian_blocking_findings=[finding])
    assert "deterministic, model-independent scan" in rendered
    assert "SECRET-001" in rendered
    assert "BLOCKING" in rendered
