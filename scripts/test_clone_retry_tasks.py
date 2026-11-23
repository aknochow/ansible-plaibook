# -*- coding: utf-8 -*-
"""The clone tasks must keep the condition tests/test_clone_transient_retry.yml runs."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "roles" / "review" / "tasks"


def _tasks(items):
    for item in items:
        yield item
        for key in ("tasks", "block", "rescue", "always"):
            yield from _tasks(item.get(key) or [])


def _load(path: Path) -> list[dict]:
    return list(_tasks(yaml.safe_load(path.read_text(encoding="utf-8"))))


def _argv(task: dict) -> list[str]:
    command = task.get("ansible.builtin.command")
    return command.get("argv", []) if isinstance(command, dict) else []


def _clones(path: Path) -> list[dict]:
    return [task for task in _load(path) if "ansible.builtin.git" in task or _argv(task)[:2] == ["git", "clone"]]


def _retried(path: Path) -> list[dict]:
    return [task for task in _load(path) if "until" in task]


def _condition(task: dict) -> str:
    named = re.sub(rf"(?<!\w){re.escape(task['register'])}(?!\w)", "RESULT", task["until"])
    return " ".join(named.split())


def _tested_condition() -> str:
    tested = _clones(ROOT / "tests" / "test_clone_transient_retry.yml")
    assert len(tested) == 4
    conditions = {_condition(task) for task in tested}
    assert len(conditions) == 1
    return conditions.pop()


@pytest.mark.parametrize(
    "name",
    ["clone_target_pr.yml", "prefetch_review_objects.yml", "resolve_target_branch.yml"],
)
def test_network_tasks_use_the_tested_retry_condition(name):
    tested = _tested_condition()
    tasks = _retried(TASKS / name)
    assert tasks
    for task in tasks:
        assert _condition(task) == tested
        assert task["retries"] == "{{ review_clone_retries | int }}"
        assert task["delay"] == "{{ review_clone_retry_delay | int }}"
        assert task["async"] == "{{ review_clone_timeout | int }}"


@pytest.mark.parametrize("name", ["clone_target_pr.yml", "resolve_target_branch.yml"])
def test_clone_task_has_the_configured_timeout(name):
    clones = _clones(TASKS / name)
    assert clones
    for clone in clones:
        assert clone["async"] == "{{ review_clone_timeout | int }}"


def test_pr_clone_is_a_blobless_partial_clone():
    (clone,) = _clones(TASKS / "clone_target_pr.yml")
    argv = _argv(clone)
    assert "--filter=blob:none" in argv
    assert "--no-checkout" in argv
    assert argv.index("--") < argv.index("{{ review_clone_url }}")


def test_pr_resolver_prefetches_after_merge_base_and_before_the_sandbox_copy():
    names = [
        task.get("ansible.builtin.include_tasks") or task.get("name")
        for task in _load(TASKS / "resolve_target_pr.yml")
    ]
    assert "ansible.builtin.git" not in (TASKS / "resolve_target_pr.yml").read_text(encoding="utf-8")
    clone = names.index("clone_target_pr.yml")
    merge_base = names.index("Compute the merge base between the target branch and the PR/MR head")
    prefetch = names.index("prefetch_review_objects.yml")
    copy = names.index("copy_to_sandbox.yml")
    assert clone < merge_base < prefetch < copy
