# -*- coding: utf-8 -*-
"""The cursor provider is gone from the playbook and the package."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_review_yml_has_no_cursor_sidecar():
    text = (_ROOT / "review.yml").read_text(encoding="utf-8")
    assert "cursor" not in text.lower()


def test_no_cursor_task_files_remain():
    tasks = _ROOT / "roles/review/tasks"
    assert not [p.name for p in tasks.iterdir() if "cursor" in p.name]


def test_cursor_sdk_is_not_a_dependency():
    # Text check: tomllib is 3.11+ and plaibook still supports 3.10.
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "cursor-sdk" not in text
    assert "\ncursor = [" not in text


def test_preflight_rejects_cursor_family():
    text = (_ROOT / "roles/review/tasks/provider_preflight.yml").read_text(encoding="utf-8")
    assert "agent_family in ['claude', 'claude_cli', 'gemini', 'openai', 'xai']" in text
