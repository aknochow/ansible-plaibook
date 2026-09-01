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


def test_fetch_gitlab_mock():
    mr_data = {
        "iid": 15,
        "title": "GitLab MR Title",
        "author": {"username": "gluser"},
        "state": "opened",
        "source_branch": "feature-gl",
        "target_branch": "main",
        "web_url": "https://gitlab.example.com/org/repo/-/merge_requests/15",
        "description": "MR description",
        "sha": "gl1234567890abcdef",
    }
    changes_data = {
        "changes": [{"new_path": "playbook.yml", "old_path": "playbook.yml"}]
    }

    def mock_http_get(url, headers=None, timeout=60):
        if url.endswith("/merge_requests/15"):
            return 200, mr_data, {}
        elif url.endswith("/merge_requests/15/changes"):
            return 200, changes_data, {}
        elif "/pipelines" in url:
            return 200, [], {}
        elif "/discussions" in url:
            return 200, [], {}
        return 404, None, {}

    with patch("fetch_pr_context.http_get", side_effect=mock_http_get):
        result = fetch_gitlab("gitlab.example.com", "org/repo", 15)

    assert result["identifier"] == "MR !15"
    assert result["author"] == "gluser"
    assert result["target_branch"] == "main"
    assert result["head_sha"] == "gl1234567890abcdef"
    assert len(result["files_changed"]) == 1
    assert result["files_changed"][0]["path"] == "playbook.yml"


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
    assert result["target_branch"] == "main"
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
    assert result["target_branch"] == "main"
    assert result["ci_status"] == "passing"
    assert len(result["files_changed"]) == 1
    assert result["files_changed"][0]["path"] == "ansible.yml"
    assert len(result["unresolved_comments"]) == 1


def test_fetch_gitea_file_statuses_and_deletions():
    pr_data = {
        "number": 11,
        "title": "Refactor and remove deprecated files",
        "user": {"login": "giteauser"},
        "state": "open",
        "head": {"ref": "feature-cleanup", "sha": "abc123456789"},
        "base": {"ref": "main"},
        "html_url": "https://gitea.example.com/org/repo/pulls/11",
        "body": "PR description",
    }
    files_data = [
        {"filename": "roles/awx/templates/awx-instance.yaml.j2", "status": "deleted", "additions": 0, "deletions": 45},
        {"filename": "docs/old_name.md", "status": "renamed", "additions": 2, "deletions": 1},
        {"filename": "admin/wiki-lint", "status": "added", "additions": 135, "deletions": 0},
        {"filename": ".gitignore", "status": "modified", "additions": 3, "deletions": 0},
    ]

    def mock_http_get(url, headers=None, timeout=60):
        if url.endswith("/pulls/11"):
            return 200, pr_data, {}
        elif "files?limit=50&page=1" in url:
            return 200, files_data, {}
        elif "/commits/abc123456789/statuses" in url:
            return 200, [], {}
        elif "/pulls/11/reviews" in url:
            return 200, [], {}
        return 404, None, {}

    with patch("fetch_pr_context.http_get", side_effect=mock_http_get):
        result = fetch_gitea("gitea.example.com", "org/repo", 11)

    assert len(result["files_changed"]) == 4
    file_map = {f["path"]: f for f in result["files_changed"]}

    # Verify deleted file is preserved and marked as 'Deleted'
    assert "roles/awx/templates/awx-instance.yaml.j2" in file_map
    assert file_map["roles/awx/templates/awx-instance.yaml.j2"]["status"] == "Deleted"
    assert file_map["roles/awx/templates/awx-instance.yaml.j2"]["deletions"] == 45

    # Verify renamed, added, modified
    assert file_map["docs/old_name.md"]["status"] == "Renamed"
    assert file_map["admin/wiki-lint"]["status"] == "Added"
    assert file_map[".gitignore"]["status"] == "Modified"


