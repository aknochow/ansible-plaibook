# -*- coding: utf-8 -*-
"""GitHub check planning. No network."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "github_review_check.py"
    spec = importlib.util.spec_from_file_location("github_review_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_keyword_and_association():
    mod = _load()
    assert mod.is_review_command("/plai-review")
    assert mod.is_review_command("  /plai-review again")
    assert not mod.is_review_command("please /plai-review")
    assert mod.comment_allowed("OWNER", "User")
    assert not mod.comment_allowed("NONE", "User")
    assert not mod.comment_allowed("OWNER", "Bot")


def test_in_progress_checks_are_not_done_and_our_check_is_ignored():
    mod = _load()
    pending = mod.other_work_pending(
        workflow_runs=[{"name": "CI", "status": "completed"}, {"name": "CodeQL", "status": "in_progress"}],
        check_runs=[{"name": "plaibook review", "status": "in_progress"}, {"name": "Test", "status": "completed"}],
        statuses=[{"context": "plaibook review", "state": "pending"}],
        required_workflows={"CI", "CodeQL"},
    )
    assert pending == ["CodeQL"]


def test_missing_required_workflow_is_not_done():
    mod = _load()
    pending = mod.other_work_pending(
        workflow_runs=[{"name": "CI", "status": "completed"}],
        check_runs=[],
        statuses=[],
        required_workflows={"CI", "CodeQL"},
    )
    assert "missing:CodeQL" in pending


def test_plan_updates_same_fingerprint_and_skips_identical_body():
    mod = _load()
    existing = [{"id": 7, "body": "old\n<!-- plaibook-finding:abcdabcdabcdabcd -->"}]
    same = {"fingerprint": "abcdabcdabcdabcd", "body": existing[0]["body"]}
    changed = {"fingerprint": "abcdabcdabcdabcd", "body": "new\n<!-- plaibook-finding:abcdabcdabcdabcd -->"}
    fresh = {"fingerprint": "ffffeeeeffffeeee", "body": "new finding"}
    create, update = mod.plan_comments(existing, [same])
    assert create == [] and update == []
    create, update = mod.plan_comments(existing, [changed, fresh])
    assert create == [fresh]
    assert update == [(7, changed)]


def test_conclusion_fails_when_no_review_ran():
    mod = _load()
    assert mod.check_conclusion({"status": "ok", "targets": []}) == "failure"
    assert mod.check_conclusion({"status": "failed", "targets": [{"verdict": "READY_FOR_HUMAN_REVIEW"}]}) == "failure"
    assert mod.check_conclusion({"status": "ok", "targets": [{"verdict": "NEEDS_CHANGES"}]}) == "failure"
    assert mod.check_conclusion({"status": "ok", "targets": [{"verdict": "READY_FOR_HUMAN_REVIEW"}]}) == "success"


def test_redact_does_not_keep_token_prefixes():
    mod = _load()
    assert "github_pat_secretvalue" not in mod.redact("boom github_pat_secretvalue trailing")
    assert "[redacted]" in mod.redact("boom github_pat_secretvalue trailing")
