# -*- coding: utf-8 -*-
"""Unit tests for post_review_comment.py's own charset guards
(_SAFE_PROJECT_RE/_SAFE_HOST_RE) -- the network-calling functions
(post_gitlab/post_github) need real gh/glab CLI auth and aren't
unit-testable without mocking subprocess extensively.

Run with: uv run pytest roles/review/files/pr_review/
"""
from __future__ import annotations

from post_review_comment import _SAFE_HOST_RE, _SAFE_PROJECT_RE


def test_safe_project_accepts_normal_org_repo():
    assert _SAFE_PROJECT_RE.match("org/repo")
    assert _SAFE_PROJECT_RE.match("group/subgroup/repo")


def test_safe_project_rejects_leading_hyphen():
    # A leading '-' would be parsed as a glab/gh CLI flag, not a
    # literal project path.
    assert not _SAFE_PROJECT_RE.match("-x/repo")


def test_safe_project_rejects_unsafe_chars():
    assert not _SAFE_PROJECT_RE.match("org/repo%00")
    assert not _SAFE_PROJECT_RE.match("../../../etc/passwd")


def test_safe_host_accepts_normal_hostname():
    assert _SAFE_HOST_RE.match("gitlab.example.com")


def test_safe_host_rejects_leading_hyphen():
    assert not _SAFE_HOST_RE.match("-evil.example.com")


def test_safe_host_rejects_unsafe_chars():
    assert not _SAFE_HOST_RE.match("gitlab.example.com/../../etc")
