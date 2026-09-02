# -*- coding: utf-8 -*-
"""Behavioral tests for check_last_run_target.py.

Run with: python3 -m pytest scripts/
"""
from __future__ import annotations

import json

from check_last_run_target import check_last_run_target, main


def _write(tmp_path, content, filename="last_run.json"):
    path = tmp_path / filename
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return str(path)


def test_matching_target_found(tmp_path):
    path = _write(
        tmp_path,
        {
            "run_id": "abc123",
            "targets": [{"target": "org/repo#1", "verdict": "READY_FOR_HUMAN_REVIEW", "score": 100.0}],
        },
    )
    ok, message, match = check_last_run_target("org/repo#1", path)
    assert ok is True
    assert message == ""
    assert match == {"target": "org/repo#1", "verdict": "READY_FOR_HUMAN_REVIEW", "score": 100.0}


def test_mismatched_target_refused_loudly(tmp_path):
    path = _write(
        tmp_path,
        {
            "run_id": "xyz789",
            "targets": [{"target": "aknochow/ansible-plaibook#7a0f849", "verdict": "READY_FOR_HUMAN_REVIEW"}],
        },
    )
    ok, message, match = check_last_run_target("other-org/other-repo#771bc27", path)
    assert ok is False
    assert match is None
    assert "other-org/other-repo#771bc27" in message
    assert "aknochow/ansible-plaibook#7a0f849" in message
    assert "MISMATCH" in message


def test_multiple_targets_one_matches(tmp_path):
    path = _write(
        tmp_path,
        {
            "run_id": "abc123",
            "targets": [
                {"target": "org/repo#1", "verdict": "NEEDS_CHANGES"},
                {"target": "org/repo#2", "verdict": "READY_FOR_HUMAN_REVIEW"},
            ],
        },
    )
    ok, _message, match = check_last_run_target("org/repo#2", path)
    assert ok is True
    assert match["verdict"] == "READY_FOR_HUMAN_REVIEW"


def test_summary_json_format_matching(tmp_path):
    path = _write(
        tmp_path,
        {
            "target": "aknochow.openshell#10",
            "commit": "0c2c62c",
            "branch": "main",
            "date": "2026-08-06",
            "verdict": "READY_FOR_HUMAN_REVIEW",
            "score_overall": 95.0,
        },
        filename="summary.json",
    )
    ok, message, match = check_last_run_target(
        "aknochow.openshell#10",
        path,
        expected_commit="0c2c62c",
        expected_branch="main",
    )
    assert ok is True
    assert message == ""
    assert match["verdict"] == "READY_FOR_HUMAN_REVIEW"
    assert match["score_overall"] == 95.0


def test_summary_json_mismatch_refused(tmp_path):
    path = _write(
        tmp_path,
        {
            "target": "aknochow.openshell#10",
            "commit": "0c2c62c",
            "branch": "main",
            "verdict": "READY_FOR_HUMAN_REVIEW",
        },
        filename="summary.json",
    )
    ok, message, match = check_last_run_target(
        "aknochow.openshell#10",
        path,
        expected_commit="different_sha",
    )
    assert ok is False
    assert match is None
    assert "MISMATCH" in message
    assert "different_sha" in message


def test_missing_file(tmp_path):
    path = str(tmp_path / "does-not-exist.json")
    ok, message, match = check_last_run_target("org/repo#1", path)
    assert ok is False
    assert match is None
    assert "does not exist" in message


def test_invalid_json(tmp_path):
    path = _write(tmp_path, "{not valid json")
    ok, message, match = check_last_run_target("org/repo#1", path)
    assert ok is False
    assert match is None
    assert "not valid JSON" in message


def test_empty_targets_list(tmp_path):
    path = _write(tmp_path, {"run_id": "abc123", "targets": []})
    ok, message, match = check_last_run_target("org/repo#1", path)
    assert ok is False
    assert match is None
    assert "no targets recorded" in message


def test_non_dict_json(tmp_path):
    path = _write(tmp_path, ["item1", "item2"])
    ok, message, match = check_last_run_target("org/repo#1", path)
    assert ok is False
    assert "not a JSON object" in message


def test_cli_exit_code_success(tmp_path, capsys):
    path = _write(
        tmp_path,
        {"run_id": "abc123", "targets": [{"target": "org/repo#1", "verdict": "READY_FOR_HUMAN_REVIEW"}]},
    )
    rc = main(["org/repo#1", "--file", path])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)["target"] == "org/repo#1"


def test_cli_exit_code_mismatch(tmp_path, capsys):
    path = _write(
        tmp_path,
        {"run_id": "abc123", "targets": [{"target": "org/repo#1", "verdict": "READY_FOR_HUMAN_REVIEW"}]},
    )
    rc = main(["org/repo#2", "--file", path])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "MISMATCH" in captured.err


def test_cli_summary_json_with_flags(tmp_path, capsys):
    path = _write(
        tmp_path,
        {
            "target": "org/repo#42",
            "commit": "abc1234",
            "branch": "feature",
            "verdict": "READY_FOR_HUMAN_REVIEW",
        },
        filename="summary.json",
    )
    rc = main(["org/repo#42", "--file", path, "--commit", "abc1234", "--branch", "feature"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)["target"] == "org/repo#42"
