# -*- coding: utf-8 -*-
"""Behavioral tests for build_review_frontmatter.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

This plugin takes verdict/score/scores as direct arguments rather than
regexing them out of report text.
"""
from __future__ import annotations

from build_review_frontmatter import ActionModule, build_review_frontmatter

BASELINE_RESULT = (
    "---\n"
    "date: 2026-08-06\n"
    "commit: abc1234\n"
    'project: "org/repo"\n'
    'branch: "feat/my-branch"\n'
    "verdict: READY_FOR_HUMAN_REVIEW\n"
    "score: 9.0\n"
    "scores:\n"
    "  functionality: 9.0\n"
    "  security: 8.5\n"
    "  quality: 9.5\n"
    "---\n"
)


def test_matches_real_harness_baseline():
    result = build_review_frontmatter(
        today_date="2026-08-06",
        commit="abc1234",
        project="org/repo",
        branch="feat/my-branch",
        verdict="READY_FOR_HUMAN_REVIEW",
        score_overall=9.0,
        score_functionality=9.0,
        score_security=8.5,
        score_quality=9.5,
    )
    assert not result == BASELINE_RESULT


def test_double_quote_is_stripped_from_project_and_branch():
    result = build_review_frontmatter(
        today_date="2026-08-06",
        commit="def5678",
        project='org/re"po',
        branch='feat/we"ird',
        verdict="NEEDS_CHANGES",
        score_overall=50.0,
        score_functionality=50.0,
        score_security=50.0,
        score_quality=50.0,
    )
    assert 'project: "org/repo"' in result
    assert 'branch: "feat/weird"' in result
    assert result.count('"') == 4  # exactly the two project/branch quote pairs, nothing else


def test_newline_is_stripped_from_project_and_branch():
    # A newline in project/branch could otherwise break out of the
    # quoted YAML value and inject an arbitrary frontmatter key on the
    # next line.
    result = build_review_frontmatter(
        today_date="2026-08-06",
        commit="abc1234",
        project="org/repo\nevil: injected",
        branch="feat/branch\r\nalso: injected",
        verdict="NEEDS_CHANGES",
        score_overall=1.0,
        score_functionality=1.0,
        score_security=1.0,
        score_quality=1.0,
    )
    assert 'project: "org/repoevil: injected"' in result
    assert "\nevil: injected" not in result
    assert "\nalso: injected" not in result


def test_score_values_are_coerced_to_float_even_if_passed_as_int():
    # The Ansible call site always applies `| float` first, but a
    # non-Ansible caller passing a bare int must not produce "score: 9"
    # instead of "score: 9.0".
    result = build_review_frontmatter(
        today_date="2026-08-06", commit="abc1234", project="a/b", branch="main",
        verdict="READY_FOR_HUMAN_REVIEW",
        score_overall=9, score_functionality=9, score_security=9, score_quality=9,
    )
    assert "score: 9.0" in result
    assert "functionality: 9.0" in result


def test_a_non_round_fractional_score_does_not_produce_a_long_decimal_tail():
    # Python's default float repr can produce an arbitrarily-long
    # decimal for a non-round value. Not reachable via the real call
    # path (compute_review_scores.py always rounds to 1 decimal first),
    # but this function must format defensively regardless.
    result = build_review_frontmatter(
        today_date="2026-08-06", commit="abc1234", project="a/b", branch="main",
        verdict="NEEDS_CHANGES",
        score_overall=86.66666666666667, score_functionality=86.66666666666667,
        score_security=86.66666666666667, score_quality=86.66666666666667,
    )
    assert "score: 86.7" in result
    assert "86.66666666666667" not in result


def test_verdict_score_and_scores_are_always_present():
    result = build_review_frontmatter(
        today_date="2026-08-06",
        commit="abc1234",
        project="org/repo",
        branch="main",
        verdict="READY_FOR_HUMAN_REVIEW",
        score_overall=100.0,
        score_functionality=100.0,
        score_security=100.0,
        score_quality=100.0,
    )
    assert "verdict: READY_FOR_HUMAN_REVIEW" in result
    assert "score: 100.0" in result
    assert "scores:" in result
    assert "  functionality: 100.0" in result
    assert "  security: 100.0" in result
    assert "  quality: 100.0" in result


def test_output_starts_and_ends_with_yaml_frontmatter_delimiters():
    result = build_review_frontmatter(
        today_date="2026-08-06", commit="abc1234", project="a/b", branch="main",
        verdict="NEEDS_CHANGES", score_overall=1.0,
        score_functionality=1.0, score_security=1.0, score_quality=1.0,
    )
    lines = result.split("\n")
    assert lines[0] == "---"
    assert lines[-2] == "---"
    assert lines[-1] == ""


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "build_review_frontmatter"
        self.async_val = False
        self.check_mode = False


_ALL_ARGS = {
    "today_date": "2026-08-06",
    "commit": "abc1234",
    "project": "org/repo",
    "branch": "main",
    "verdict": "READY_FOR_HUMAN_REVIEW",
    "score_overall": 9.0,
    "score_functionality": 9.0,
    "score_security": 9.0,
    "score_quality": 9.0,
}


def _run_action_module(args):
    action = ActionModule(
        task=_FakeTask(args),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(_ALL_ARGS)
    assert "failed" not in result
    assert result["frontmatter"] == build_review_frontmatter(**_ALL_ARGS)


def test_action_module_requires_all_args():
    incomplete = dict(_ALL_ARGS)
    del incomplete["score_quality"]
    result = _run_action_module(incomplete)
    assert result["failed"] is True
