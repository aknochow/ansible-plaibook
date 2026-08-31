# -*- coding: utf-8 -*-
"""Unit tests for fetch_pr_context.py (parse_url, REST HTTP handlers, pagination).

Run with: uv run --with pytest --with pyyaml --with ansible-core pytest roles/review/files/pr_review/test_fetch_pr_context.py
"""
from __future__ import annotations

from unittest.mock import patch

from fetch_pr_context import fetch_gitea, fetch_github, fetch_gitlab, parse_url


def test_github_pull_url():
    assert parse_url("https://github.com/org/repo/pull/42") == ("github", "github.com", "org/repo", 42)


def test_gitlab_merge_request_url():
    assert parse_url("https://gitlab.example.com/org/repo/-/merge_requests/7") == ("gitlab", "gitlab.example.com", "org/repo", 7)


def test_gitlab_subgroup_merge_request_url():
    assert parse_url("https://gitlab.example.com/group/subgroup/repo/-/merge_requests/3") == (
        "gitlab", "gitlab.example.com", "group/subgroup/repo", 3,
    )


def test_gitea_pull_urls():
    assert parse_url("https://gitea.example.com/org/repo/pulls/12") == ("gitea", "gitea.example.com", "org/repo", 12)
    assert parse_url("https://gitea.example.com/org/repo/pull/12") == ("gitea", "gitea.example.com", "org/repo", 12)


def test_unparseable_url_returns_none():
    assert parse_url("not a url") is None
    assert parse_url("https://example.com/org/repo/issues/1") is None


def test_bare_org_repo_number_form_is_not_a_url_and_returns_none():
    assert parse_url("org/repo#42") is None
    assert parse_url("gitea:org/repo#42") is None


def test_unsafe_hostname_or_project_chars_fail_to_parse():
    assert parse_url("https://gitlab.example.com/-x/repo/-/merge_requests/1") is None
    assert parse_url("https://gitlab.example.com/org/repo%00/-/merge_requests/1") is None
    assert parse_url("https://-evil.example.com/org/repo/-/merge_requests/1") is None


def test_fetch_github_with_pagination():
    pr_data = {
        "number": 42,
        "title": "Fix bug in parser",
        "user": {"login": "octocat"},
        "state": "open",
        "head": {"ref": "fix-branch", "sha": "abcdef123456"},
        "base": {"ref": "main"},
        "html_url": "https://github.com/org/repo/pull/42",
        "body": "Fixes parser bug",
    }
    # Page 1: 100 files
    page1_files = [{"filename": f"file_{i}.py", "status": "modified", "additions": 1, "deletions": 0} for i in range(100)]
    # Page 2: 25 files (total 125 files)
    page2_files = [{"filename": f"file_{i}.py", "status": "added", "additions": 5, "deletions": 0} for i in range(100, 125)]

    status_data = {"statuses": [{"state": "success", "context": "ci/tests"}]}
    check_runs_data = {"check_runs": [{"status": "completed", "conclusion": "success", "name": "build"}]}
    comments_data = [{"user": {"login": "reviewer1"}, "path": "file_1.py", "line": 10, "body": "Please add docstring"}]

    def mock_http_get(url, headers=None, timeout=60):
        if url.endswith("/pulls/42"):
            return 200, pr_data, {}
        elif "files?per_page=100&page=1" in url:
            return 200, page1_files, {}
        elif "files?per_page=100&page=2" in url:
            return 200, page2_files, {}
        elif "/commits/abcdef123456/status" in url:
            return 200, status_data, {}
        elif "/commits/abcdef123456/check-runs" in url:
            return 200, check_runs_data, {}
        elif "/pulls/42/comments" in url:
            return 200, comments_data, {}
        return 404, None, {}

    with patch("fetch_pr_context.http_get", side_effect=mock_http_get):
        result = fetch_github("org", "repo", 42)

    assert result["identifier"] == "PR #42"
    assert result["title"] == "Fix bug in parser"
    assert result["author"] == "octocat"
    assert result["ci_status"] == "passing"
    assert len(result["files_changed"]) == 125
    assert result["files_changed"][0]["path"] == "file_0.py"
    assert result["files_changed"][105]["status"] == "Added"
    assert len(result["unresolved_comments"]) == 1
    assert result["unresolved_comments"][0]["author"] == "reviewer1"


def test_fetch_gitea_mock():
    pr_data = {
        "number": 10,
        "title": "Add feature",
        "user": {"login": "giteauser"},
        "state": "open",
        "head": {"ref": "feature-x", "sha": "1234567890ab"},
        "base": {"ref": "main"},
        "html_url": "https://gitea.example.com/org/repo/pulls/10",
        "body": "PR description",
    }
    files_data = [{"filename": "ansible.yml", "status": "modified", "additions": 10, "deletions": 2}]
    statuses_data = [{"status": "success", "context": "continuous-integration"}]
    reviews_data = [{"id": 1}]
    review_comments = [{"user": {"login": "lead"}, "path": "ansible.yml", "line": 5, "body": "Looks good"}]

    def mock_http_get(url, headers=None, timeout=60):
        if url.endswith("/pulls/10"):
            return 200, pr_data, {}
        elif "files?limit=50&page=1" in url:
            return 200, files_data, {}
        elif "/commits/1234567890ab/statuses" in url:
            return 200, statuses_data, {}
        elif "/pulls/10/reviews" in url:
            return 200, reviews_data, {}
        elif "/pulls/10/reviews/1/comments" in url:
            return 200, review_comments, {}
        return 404, None, {}

    with patch("fetch_pr_context.http_get", side_effect=mock_http_get):
        result = fetch_gitea("gitea.example.com", "org/repo", 10)

    assert result["identifier"] == "PR #10"
    assert result["author"] == "giteauser"
    assert result["ci_status"] == "passing"
    assert len(result["files_changed"]) == 1
    assert result["files_changed"][0]["path"] == "ansible.yml"
    assert len(result["unresolved_comments"]) == 1

