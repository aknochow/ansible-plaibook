# -*- coding: utf-8 -*-
"""Unit tests for fetch_pr_context.py's pure logic (parse_url) --
the network-calling functions (fetch_github/fetch_gitlab) need real
gh/glab CLI auth and aren't unit-testable without mocking subprocess
extensively; see the module's own docstring for how this was verified
against a real public PR instead (octocat/Spoon-Knife).

Run with: cd roles/review/files/pr_review && python3 -m pytest .
(not part of `uv run pytest action_plugins/` -- this directory holds
standalone CLI scripts invoked via argv:, not action plugins).
"""
from __future__ import annotations

from fetch_pr_context import parse_url


def test_github_pull_url():
    assert parse_url("https://github.com/org/repo/pull/42") == ("github", "github.com", "org/repo", 42)


def test_gitlab_merge_request_url():
    assert parse_url("https://gitlab.example.com/org/repo/-/merge_requests/7") == ("gitlab", "gitlab.example.com", "org/repo", 7)


def test_gitlab_subgroup_merge_request_url():
    assert parse_url("https://gitlab.example.com/group/subgroup/repo/-/merge_requests/3") == (
        "gitlab", "gitlab.example.com", "group/subgroup/repo", 3,
    )


def test_unparseable_url_returns_none():
    assert parse_url("not a url") is None
    assert parse_url("https://example.com/org/repo/issues/1") is None


def test_bare_org_repo_number_form_is_not_a_url_and_returns_none():
    # This script only accepts a fully-qualified URL -- the caller
    # (resolve_target_pr.yml) always synthesizes one before calling it.
    assert parse_url("org/repo#42") is None


def test_unsafe_hostname_or_project_chars_fail_to_parse():
    # This function's own charset guard, independent of
    # parse_pr_target.yml's Ansible-side validation -- matters when
    # this script is run standalone, outside the pipeline.
    assert parse_url("https://gitlab.example.com/-x/repo/-/merge_requests/1") is None
    assert parse_url("https://gitlab.example.com/org/repo%00/-/merge_requests/1") is None
    assert parse_url("https://-evil.example.com/org/repo/-/merge_requests/1") is None
