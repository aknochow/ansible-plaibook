# -*- coding: utf-8 -*-
"""Behavioral tests for parse_diff_stat.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

RAW_NUMSTAT_OUTPUT is real `git diff --numstat` output against a fixture
repo covering a modified file, a renamed file, an added file, and a
deleted file. BASELINE_STATS is the expected parsed result.
"""
from __future__ import annotations

from parse_diff_stat import ActionModule, parse_diff_stat

RAW_NUMSTAT_OUTPUT = (
    "0\t1\tREADME.md\n"
    "2\t0\tsrc/brand_new.py\n"
    "5\t0\tsrc/module.py\n"
    "0\t0\tsrc/{old_name.py => new_name.py}\n"
    "2\t0\ttests/test_module.py\n"
)

BASELINE_STATS = {
    "README.md": {"additions": 0, "deletions": 1},
    "src/brand_new.py": {"additions": 2, "deletions": 0},
    "src/module.py": {"additions": 5, "deletions": 0},
    "src/new_name.py": {"additions": 0, "deletions": 0},
    "tests/test_module.py": {"additions": 2, "deletions": 0},
}


def test_matches_real_harness_baseline():
    assert parse_diff_stat(RAW_NUMSTAT_OUTPUT) == BASELINE_STATS


def test_rename_shorthand_resolves_to_the_new_path_only():
    # git's `{old => new}` shorthand for a rename with a common path
    # prefix/suffix must resolve to just the new full path, not the
    # literal shorthand string.
    result = parse_diff_stat("0\t0\tsrc/{old_name.py => new_name.py}\n")
    assert list(result.keys()) == ["src/new_name.py"]


def test_rename_shorthand_with_common_prefix_and_suffix():
    # A rename shorthand can have text both before AND after the braces
    # when the common parts are on both ends, e.g. renaming a directory
    # component in the middle of a path.
    result = parse_diff_stat("1\t1\tsrc/{old => new}/module.py\n")
    assert list(result.keys()) == ["src/new/module.py"]


def test_binary_file_dash_counts_become_zero_not_an_error():
    # git prints '-' for both columns on a binary file (no line-level
    # stats computed) -- matches the original's ternary exactly.
    result = parse_diff_stat("-\t-\tassets/logo.png\n")
    assert result == {"assets/logo.png": {"additions": 0, "deletions": 0}}


def test_malformed_line_with_wrong_field_count_is_skipped():
    # A line that doesn't split into exactly 3 tab-separated fields is
    # silently skipped, matching the original's `if len(parts) == 3`
    # guard -- not an error condition worth raising for.
    result = parse_diff_stat("not a valid numstat line\n5\t2\treal_file.py\n")
    assert result == {"real_file.py": {"additions": 5, "deletions": 2}}


def test_empty_output_returns_empty_dict():
    assert parse_diff_stat("") == {}


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "parse_diff_stat"
        self.async_val = False
        self.check_mode = False


def _run_action_module(numstat_output):
    action = ActionModule(
        task=_FakeTask({"numstat_output": numstat_output}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(RAW_NUMSTAT_OUTPUT)
    assert "failed" not in result
    assert result["stats"] == BASELINE_STATS


def test_action_module_requires_numstat_output_arg():
    action = ActionModule(
        task=_FakeTask({}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
