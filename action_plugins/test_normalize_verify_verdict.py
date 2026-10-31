# -*- coding: utf-8 -*-
"""Behavioral tests for normalize_verify_verdict.py."""
from __future__ import annotations

import pytest
from normalize_verify_verdict import (
    UNSUPPORTED_RATIONALE,
    ActionModule,
    is_placeholder_text,
    normalize_verify_verdict,
)

REAL_VERDICT = {
    "evidence_status": "verified",
    "evidence": "verify_result_severity_assessed: true",
    "rationale": "The flag is set before any axis field is checked.",
    "requires_execution": False,
    "reachability": "realistic",
    "trust_boundary": "crosses-trust-boundary",
    "worst_outcome_category": "data-corruption",
    "silent_failure": True,
}


@pytest.mark.parametrize(
    "value",
    [
        "", "   ", "placeholder", "Placeholder.", " <placeholder> ", "[PLACEHOLDER]", "`placeholder`",
        "placeholder rationale", "TBD", "TODO", "n/a", "N/A", "none", "null", "...", "-", "xxx", None, 7, ["x"],
    ],
)
def test_empty_and_filler_values_are_placeholders(value):
    assert is_placeholder_text(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "The placeholder value reaches the merge task unchanged.",
        "verify_result_evidence: placeholder",
        "This is not a placeholder: the branch runs on every turn.",
        "TODO comment on line 12 is the only reference.",
        "return None",
        "x = 1",
        "ok",
    ],
)
def test_real_text_mentioning_a_filler_word_is_not_a_placeholder(value):
    assert is_placeholder_text(value) is False


@pytest.mark.parametrize("status", ["verified", "refuted", "inconclusive"])
@pytest.mark.parametrize("field", ["evidence", "rationale"])
@pytest.mark.parametrize("value", ["", "placeholder"])
def test_placeholder_field_makes_any_verdict_inconclusive_and_unassessed(status, field, value):
    result = normalize_verify_verdict({**REAL_VERDICT, "evidence_status": status, field: value})
    assert result["status"] == "inconclusive"
    assert result["severity_assessed"] is False
    assert result["rationale"] == UNSUPPORTED_RATIONALE
    assert result["worst_outcome_category"] == "cosmetic-style"
    assert result["silent_failure"] is False


def test_real_evidence_is_kept_when_only_the_rationale_is_a_placeholder():
    result = normalize_verify_verdict({**REAL_VERDICT, "rationale": "placeholder"})
    assert result["evidence"] == REAL_VERDICT["evidence"]


def test_placeholder_evidence_is_cleared():
    assert normalize_verify_verdict({**REAL_VERDICT, "evidence": "placeholder"})["evidence"] == ""


@pytest.mark.parametrize("field", ["evidence", "rationale", "evidence_status"])
def test_missing_core_field_is_inconclusive(field):
    verdict = {key: value for key, value in REAL_VERDICT.items() if key != field}
    assert normalize_verify_verdict(verdict)["status"] == "inconclusive"


def test_status_outside_the_schema_enum_is_inconclusive():
    assert normalize_verify_verdict({**REAL_VERDICT, "evidence_status": "confirmed"})["status"] == "inconclusive"


@pytest.mark.parametrize("verdict", [None, "", "not a mapping", []])
def test_non_mapping_input_is_inconclusive(verdict):
    result = normalize_verify_verdict(verdict)
    assert result["status"] == "inconclusive"
    assert result["severity_assessed"] is False


def test_real_verdict_passes_through_assessed():
    result = normalize_verify_verdict(REAL_VERDICT)
    assert result == {
        "status": "verified",
        "evidence": REAL_VERDICT["evidence"],
        "rationale": REAL_VERDICT["rationale"],
        "requires_execution": False,
        "severity_assessed": True,
        "reachability": "realistic",
        "trust_boundary": "crosses-trust-boundary",
        "worst_outcome_category": "data-corruption",
        "silent_failure": True,
    }


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "normalize_verify_verdict"
        self.async_val = False
        self.check_mode = False


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


def test_action_module_returns_the_normalized_values():
    result = _run_action_module({"verdict": REAL_VERDICT})
    assert "failed" not in result
    assert result["status"] == "verified"
    assert result["changed"] is False


def test_action_module_requires_the_verdict_argument():
    assert _run_action_module({})["failed"] is True
