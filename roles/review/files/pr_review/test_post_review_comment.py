# -*- coding: utf-8 -*-
"""Unit tests for post_review_comment.py (charset guards and REST posting).

Run with: uv run --with pytest --with pyyaml --with ansible-core pytest roles/review/files/pr_review/test_post_review_comment.py
"""
from __future__ import annotations

import tempfile
from unittest.mock import patch

from post_review_comment import _SAFE_HOST_RE, _SAFE_PROJECT_RE, post_gitea, post_github, post_gitlab


def test_safe_project_accepts_normal_org_repo():
    assert _SAFE_PROJECT_RE.match("org/repo")
    assert _SAFE_PROJECT_RE.match("group/subgroup/repo")


def test_safe_project_rejects_leading_hyphen():
    assert not _SAFE_PROJECT_RE.match("-x/repo")


def test_safe_project_rejects_unsafe_chars():
    assert not _SAFE_PROJECT_RE.match("org/repo%00")
    assert not _SAFE_PROJECT_RE.match("../../../etc/passwd")


def test_safe_host_accepts_normal_hostname():
    assert _SAFE_HOST_RE.match("gitlab.example.com")
    assert _SAFE_HOST_RE.match("gitea.example.com")


def test_safe_host_rejects_leading_hyphen():
    assert not _SAFE_HOST_RE.match("-evil.example.com")


def test_safe_host_rejects_unsafe_chars():
    assert not _SAFE_HOST_RE.match("gitlab.example.com/../../etc")


def test_post_github_mock():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("## Review Verdict: READY")
        findings_path = f.name

    with patch("post_review_comment.http_post", return_value=(201, "{}")) as mock_post:
        post_github("org/repo", "42", findings_path)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.github.com/repos/org/repo/issues/42/comments"
        assert args[1] == {"body": "## Review Verdict: READY"}


def test_post_gitea_mock():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("## Review Verdict: NEEDS_WORK")
        findings_path = f.name

    with patch("post_review_comment.http_post", return_value=(201, "{}")) as mock_post:
        post_gitea("org/repo", "15", findings_path, "gitea.example.com")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://gitea.example.com/api/v1/repos/org/repo/issues/15/comments"
        assert args[1] == {"body": "## Review Verdict: NEEDS_WORK"}


def test_post_gitlab_mock():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("## Review Verdict: READY")
        findings_path = f.name

    with patch("post_review_comment.http_post", return_value=(201, "{}")) as mock_post:
        post_gitlab("org/repo", "8", findings_path, "gitlab.example.com")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://gitlab.example.com/api/v4/projects/org%2Frepo/merge_requests/8/notes"
        assert args[1] == {"body": "## Review Verdict: READY"}

