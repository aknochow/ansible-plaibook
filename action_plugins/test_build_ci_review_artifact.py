# -*- coding: utf-8 -*-
"""Suggestion mapping and CI artifact shape."""
from __future__ import annotations

from build_ci_review_artifact import (
    build_ci_review_artifact,
    check_conclusion,
    comment_from_finding,
    concrete_suggestion,
)


def test_prose_fix_is_not_a_suggestion():
    assert concrete_suggestion({"fix": "Use a parameterized query."}) is None


def test_single_line_fenced_fix_is_a_suggestion():
    assert concrete_suggestion({"fix": "```\nquery(?, name)\n```"}) == "query(?, name)"


def test_multiline_fenced_fix_stays_a_comment():
    assert concrete_suggestion({"fix": "```\nline one\nline two\n```"}) is None


def test_explicit_multiline_suggestion_is_kept():
    assert concrete_suggestion({"suggestion": "alpha()\nbeta()"}) == "alpha()\nbeta()"


def test_refuted_finding_is_omitted():
    assert comment_from_finding({
        "file": "a.py",
        "line": 3,
        "severity": "Major",
        "description": "gone",
        "evidence_status": "refuted",
    }) is None


def test_unsafe_path_is_omitted():
    assert comment_from_finding({
        "file": "../secrets.env",
        "line": 1,
        "severity": "Major",
        "description": "nope",
    }) is None


def test_comment_carries_fingerprint_and_suggestion_fence():
    comment = comment_from_finding({
        "file": "app.py",
        "line": "10",
        "severity": "Major",
        "lens": "Security",
        "description": "SQL is concatenated",
        "evidence": "query(name)",
        "fix": "```\nquery(?, name)\n```",
    })
    assert comment is not None
    assert comment["line"] == 10
    assert comment["suggestion"] == "query(?, name)"
    assert "```suggestion\nquery(?, name)\n```" in comment["body"]
    assert f"<!-- plaibook-finding:{comment['fingerprint']} -->" in comment["body"]


def test_passing_verdict_is_success_and_empty_is_failure():
    ok = build_ci_review_artifact("run1", "ok", "", [{
        "target": "org/repo#1",
        "commit": "abc",
        "verdict": "READY_FOR_HUMAN_REVIEW",
        "score": 100,
        "findings": [],
    }])
    assert ok["ci_result_file"] == "ci_result.run1.json"
    assert ok["last_run_file"] == "last_run.run1.json"
    assert check_conclusion(ok) == "success"
    needs = build_ci_review_artifact("run2", "ok", "", [{
        "verdict": "NEEDS_CHANGES",
        "score": 40,
        "findings": [],
    }])
    assert check_conclusion(needs) == "failure"
    failed = build_ci_review_artifact("run3", "failed", "boom", [])
    assert check_conclusion(failed) == "failure"
    assert failed["status"] == "failed"
