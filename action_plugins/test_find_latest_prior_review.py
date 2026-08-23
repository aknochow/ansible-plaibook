# -*- coding: utf-8 -*-
"""Behavioral tests for find_latest_prior_review.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

Covers the regex-filter + reverse-sort + first-match logic against
directory names shaped `YYYY-MM-DD-<hex>`: valid entries, a
non-date-shaped name, and a digit-shaped-but-invalid-hex-suffix name
(the regex requires real hex in the suffix; \\d{2} never validates
month/day ranges).

dirs_with_findings is pre-filtered by Ansible to only directories
confirmed to contain a findings.md, so the fall-through case (newest
directory missing findings.md) is exercised by simply omitting that
entry from the input list.
"""
from __future__ import annotations

from find_latest_prior_review import ActionModule, find_latest_prior_review

BASELINE_ALL_ENTRIES = [
    "2026-08-05-9999999",
    "2026-08-03-def5678",
    "2026-08-01-abc1234",
    "not-a-date-dir",
    "2026-13-99-shorthex",
]


def test_matches_real_harness_baseline_newest_has_findings():
    # Only entries WITH findings.md are ever passed in -- all three
    # date-shaped, valid-hex entries qualify here.
    dirs_with_findings = ["2026-08-05-9999999", "2026-08-03-def5678", "2026-08-01-abc1234"]
    assert find_latest_prior_review(dirs_with_findings) == "2026-08-05-9999999"


def test_falls_through_when_newest_matching_dir_lacks_findings():
    # Mirrors the real harness scenario where the newest directory
    # exists but has no findings.md (write in progress or failed) --
    # here modeled by simply not including it in dirs_with_findings.
    dirs_with_findings = ["2026-08-03-def5678", "2026-08-01-abc1234"]
    assert find_latest_prior_review(dirs_with_findings) == "2026-08-03-def5678"


def test_non_date_shaped_name_is_excluded():
    dirs_with_findings = ["not-a-date-dir"]
    assert find_latest_prior_review(dirs_with_findings) is None


def test_digit_shaped_but_invalid_hex_suffix_is_excluded():
    # "shorthex" isn't valid lowercase hex -- the regex's trailing
    # [0-9a-f]{7,} must fail to match even though the date-shaped
    # prefix looks right.
    dirs_with_findings = ["2026-13-99-shorthex"]
    assert find_latest_prior_review(dirs_with_findings) is None


def test_short_hex_below_seven_chars_is_excluded():
    dirs_with_findings = ["2026-08-01-abc12"]
    assert find_latest_prior_review(dirs_with_findings) is None


def test_uppercase_hex_is_excluded():
    # The original's pattern is [0-9a-f], lowercase only -- git short
    # SHAs are always lowercase in practice, but confirm the boundary.
    dirs_with_findings = ["2026-08-01-ABC1234"]
    assert find_latest_prior_review(dirs_with_findings) is None


def test_empty_list_returns_none():
    assert find_latest_prior_review([]) is None


def test_lexicographic_sort_correctly_orders_iso_dates():
    # ISO date-prefixed strings sort correctly in plain lexicographic
    # (not date-aware) order -- confirm across a year boundary.
    dirs_with_findings = ["2025-12-31-aaaaaaa", "2026-01-01-bbbbbbb"]
    assert find_latest_prior_review(dirs_with_findings) == "2026-01-01-bbbbbbb"


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "find_latest_prior_review"
        self.async_val = False
        self.check_mode = False


def _run_action_module(dirs_with_findings):
    action = ActionModule(
        task=_FakeTask({"dirs_with_findings": dirs_with_findings}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_found_case():
    result = _run_action_module(["2026-08-05-9999999", "2026-08-01-abc1234"])
    assert "failed" not in result
    assert result["found"] is True
    assert result["latest_dir"] == "2026-08-05-9999999"


def test_action_module_not_found_case():
    result = _run_action_module([])
    assert "failed" not in result
    assert result["found"] is False
    assert result["latest_dir"] == ""


def test_action_module_requires_the_arg():
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
