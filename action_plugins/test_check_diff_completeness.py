# -*- coding: utf-8 -*-
"""Behavioral tests for check_diff_completeness.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

BASELINE_DIFF_CONTENT covers a deleted file (README.md) and an added
line with a TODO marker and trailing whitespace, both deliberate.
"""
from __future__ import annotations

from check_diff_completeness import ActionModule, check_diff_completeness

BASELINE_DIFF_CONTENT = (
    "diff --git a/README.md b/README.md\n"
    "deleted file mode 100644\n"
    "index abc123..0000000\n"
    "--- a/README.md\n"
    "+++ /dev/null\n"
    "@@ -1 +0,0 @@\n"
    "-# Project\n"
    "diff --git a/src/module.py b/src/module.py\n"
    "index abc123..def456 100644\n"
    "--- a/src/module.py\n"
    "+++ b/src/module.py\n"
    "@@ -1,2 +1,6 @@\n"
    " def existing_function():\n"
    "     return 1\n"
    "+\n"
    "+\n"
    "+def added_function():\n"
    "+    # TODO: refactor this   \n"
    "+    return 2\n"
)
BASELINE_DELETED_FILES = ["README.md"]

BASELINE_RESULT = {
    "deleted_files": ["README.md"],
    "todo_fixme": ["+    # TODO: refactor this   "],
    "trailing_whitespace": 1,
}


def test_matches_real_harness_baseline():
    assert check_diff_completeness(BASELINE_DIFF_CONTENT, BASELINE_DELETED_FILES) == BASELINE_RESULT


def test_removed_lines_are_never_counted_as_added():
    # A line starting with '-' (removed) must never count toward
    # todo_fixme or trailing_whitespace, even if its content would
    # otherwise match -- only '+' (added) lines are in scope.
    diff = "-# TODO: old comment being removed   \n"
    result = check_diff_completeness(diff, [])
    assert result["todo_fixme"] == []
    assert result["trailing_whitespace"] == 0


def test_diff_header_lines_starting_with_plusplusplus_are_excluded():
    # '+++' is the diff header's "new file" marker line, not an added
    # line of actual content -- must not be misdetected as an added
    # line just because it starts with '+'.
    diff = "+++ b/src/module.py\n"
    result = check_diff_completeness(diff, [])
    assert result["todo_fixme"] == []
    assert result["trailing_whitespace"] == 0


def test_all_four_keyword_variants_are_detected():
    diff = "\n".join(f"+# {kw}: something" for kw in ("TODO", "FIXME", "HACK", "XXX")) + "\n"
    result = check_diff_completeness(diff, [])
    assert len(result["todo_fixme"]) == 4


def test_todo_fixme_list_is_capped_at_ten():
    diff = "\n".join(f"+# TODO: item {i}" for i in range(15)) + "\n"
    result = check_diff_completeness(diff, [])
    assert len(result["todo_fixme"]) == 10


def test_deleted_files_passed_through_unchanged():
    result = check_diff_completeness("", ["a.py", "b.py"])
    assert result["deleted_files"] == ["a.py", "b.py"]


def test_empty_diff_and_no_deletions():
    result = check_diff_completeness("", [])
    assert result == {"deleted_files": [], "todo_fixme": [], "trailing_whitespace": 0}


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "check_diff_completeness"
        self.async_val = False
        self.check_mode = False


def _run_action_module(diff_content, deleted_files):
    action = ActionModule(
        task=_FakeTask({"diff_content": diff_content, "deleted_files": deleted_files}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(BASELINE_DIFF_CONTENT, BASELINE_DELETED_FILES)
    assert "failed" not in result
    for key in BASELINE_RESULT:
        assert result[key] == BASELINE_RESULT[key]


def test_action_module_requires_both_args():
    action = ActionModule(
        task=_FakeTask({"diff_content": ""}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
