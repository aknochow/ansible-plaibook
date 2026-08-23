# -*- coding: utf-8 -*-
"""Behavioral tests for categorize_diff_files.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

BASELINE_FILES/BASELINE_STATS/BASELINE_CATEGORIES use the same fixture
as test_parse_diff_stat.py.
"""
from __future__ import annotations

from categorize_diff_files import ActionModule, categorize_diff_files

BASELINE_FILES = [
    "README.md",
    "src/brand_new.py",
    "src/module.py",
    "src/new_name.py",
    "tests/test_module.py",
]
BASELINE_STATS = {
    "README.md": {"additions": 0, "deletions": 1},
    "src/brand_new.py": {"additions": 2, "deletions": 0},
    "src/module.py": {"additions": 5, "deletions": 0},
    "src/new_name.py": {"additions": 0, "deletions": 0},
    "tests/test_module.py": {"additions": 2, "deletions": 0},
}
BASELINE_CATEGORIES = {
    "new_logic": ["src/brand_new.py", "src/module.py"],
    "modified_logic": ["src/new_name.py"],
    "config": [],
    "docs": ["README.md"],
    "tests": ["tests/test_module.py"],
    "deleted": [],
}


def test_matches_real_harness_baseline():
    assert categorize_diff_files(BASELINE_FILES, BASELINE_STATS) == BASELINE_CATEGORIES


def test_docs_takes_priority_over_deleted_stats():
    # A deleted .md file lands in "docs", not "deleted" -- if/elif
    # ordering means the docs-suffix rule wins before the stats-based
    # deleted rule ever gets checked, even though its own stats
    # (all deletions, no additions) would also match "deleted".
    files = ["CHANGELOG.md"]
    stats = {"CHANGELOG.md": {"additions": 0, "deletions": 40}}
    result = categorize_diff_files(files, stats)
    assert result["docs"] == ["CHANGELOG.md"]
    assert result["deleted"] == []


def test_docs_suffix_starting_with_test_is_not_docs():
    # f.startswith("test") is checked by the tests rule, but docs' own
    # condition explicitly excludes "not f.startswith('test')" -- a
    # file literally named test_notes.md must land in tests, not docs.
    result = categorize_diff_files(["test_notes.md"], {})
    assert result["tests"] == ["test_notes.md"]
    assert result["docs"] == []


def test_config_exact_filenames():
    result = categorize_diff_files([".gitignore", ".pre-commit-config.yaml"], {})
    assert set(result["config"]) == {".gitignore", ".pre-commit-config.yaml"}


def test_pure_deletion_with_no_matching_extension_is_deleted():
    files = ["src/removed_module.py"]
    stats = {"src/removed_module.py": {"additions": 0, "deletions": 20}}
    result = categorize_diff_files(files, stats)
    assert result["deleted"] == ["src/removed_module.py"]


def test_pure_addition_is_new_logic():
    files = ["src/added_module.py"]
    stats = {"src/added_module.py": {"additions": 20, "deletions": 0}}
    result = categorize_diff_files(files, stats)
    assert result["new_logic"] == ["src/added_module.py"]


def test_mixed_additions_and_deletions_is_modified_logic():
    files = ["src/edited_module.py"]
    stats = {"src/edited_module.py": {"additions": 5, "deletions": 3}}
    result = categorize_diff_files(files, stats)
    assert result["modified_logic"] == ["src/edited_module.py"]


def test_missing_stats_entry_defaults_to_modified_logic():
    # A file with no entry in `stats` at all (not just zero counts)
    # falls through every stats-based rule via .get(f, {}) -- lands in
    # the final else branch, same as a 0/0 file would.
    result = categorize_diff_files(["untracked_stats.py"], {})
    assert result["modified_logic"] == ["untracked_stats.py"]


def test_empty_inputs_return_all_empty_categories():
    result = categorize_diff_files([], {})
    assert all(files == [] for files in result.values())
    assert set(result.keys()) == {"new_logic", "modified_logic", "config", "docs", "tests", "deleted"}


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "categorize_diff_files"
        self.async_val = False
        self.check_mode = False


def _run_action_module(files, stats):
    action = ActionModule(
        task=_FakeTask({"files": files, "stats": stats}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(BASELINE_FILES, BASELINE_STATS)
    assert "failed" not in result
    assert result["categories"] == BASELINE_CATEGORIES


def test_action_module_requires_both_args():
    action = ActionModule(
        task=_FakeTask({"files": []}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
