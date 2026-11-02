# -*- coding: utf-8 -*-
"""The clone tasks must keep the condition tests/test_clone_transient_retry.yml runs."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _tasks(items):
    for item in items:
        yield item
        for key in ("tasks", "block", "rescue", "always"):
            yield from _tasks(item.get(key) or [])


def _clones(path: Path) -> list[dict]:
    tasks = _tasks(yaml.safe_load(path.read_text(encoding="utf-8")))
    return [task for task in tasks if "ansible.builtin.git" in task]


def _condition(task: dict) -> str:
    named = re.sub(rf"(?<!\w){re.escape(task['register'])}(?!\w)", "RESULT", task["until"])
    return " ".join(named.split())


@pytest.mark.parametrize("name", ["resolve_target_pr.yml", "resolve_target_branch.yml"])
def test_clone_task_uses_the_tested_retry_condition(name):
    tested = _clones(ROOT / "tests" / "test_clone_transient_retry.yml")
    clones = _clones(ROOT / "roles" / "review" / "tasks" / name)
    assert len(tested) == 2
    assert clones
    for clone in clones:
        assert _condition(clone) == _condition(tested[0])
        assert clone["retries"] == "{{ review_clone_retries | int }}"
        assert clone["delay"] == "{{ review_clone_retry_delay | int }}"
