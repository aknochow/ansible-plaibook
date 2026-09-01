# -*- coding: utf-8 -*-
"""Behavioral tests for prepare_findings_for_verification.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

BASELINE_RESULT matches the real verify.yml Jinja expressions this
plugin replaces:

    {{ {'evidence_status': None, 'requires_execution': False} | combine(item) }}
    {{ item | combine({'_verify_index': idx}) }}

severity_status/original_severity/_verify_eligible and
used_static_reachability_trace are later additions with no Jinja
equivalent to match against.
"""
from __future__ import annotations

import pytest
from prepare_findings_for_verification import (
    ActionModule,
    InvalidSecurityPatternError,
    prepare_findings_for_verification,
)

VERIFY_SEVERITIES = ["Critical", "Major"]
SECURITY_PATTERN = r"(?i)(password|secret|token|credential|auth|inject|traversal|sandbox|subprocess|ssh)"

BASELINE_FIXTURE_FINDINGS = [
    {"file": "a.py", "line": 10, "severity": "Major", "lens": "Security", "evidence": "e1"},
    {
        "file": "b.py", "line": 20, "severity": "Minor", "lens": "Quality", "evidence": "e2",
        "evidence_status": "already_set", "requires_execution": True,
    },
]

# finding_id is asserted separately since a real run assigns a random
# uuid -- _fixed_id_factory below makes it deterministic for this
# exact-equality comparison instead.
BASELINE_RESULT = [
    {
        "evidence_status": None, "requires_execution": False,
        "severity_status": None, "original_severity": None,
        "used_static_reachability_trace": False,
        "continues_finding_id": None, "continuity_status": None, "continuity_rationale": None,
        "file": "a.py", "line": 10, "severity": "Major", "lens": "Security", "evidence": "e1",
        "_verify_index": 0, "_verify_eligible": True, "finding_id": "id-0",
    },
    {
        "evidence_status": "already_set", "requires_execution": True,
        "severity_status": None, "original_severity": None,
        "used_static_reachability_trace": False,
        "continues_finding_id": None, "continuity_status": None, "continuity_rationale": None,
        "file": "b.py", "line": 20, "severity": "Minor", "lens": "Quality", "evidence": "e2",
        "_verify_index": 1, "_verify_eligible": False, "finding_id": None,
    },
]


def _fixed_id_factory():
    """Deterministic id_factory for exact-equality baseline tests -- 'id-0', 'id-1', ..."""
    counter = iter(range(1000))

    def factory():
        return f"id-{next(counter)}"

    return factory


def _prepare(findings, verify_severities=VERIFY_SEVERITIES, security_sensitive_pattern=SECURITY_PATTERN, id_factory=None):
    kwargs = {"id_factory": id_factory} if id_factory is not None else {}
    return prepare_findings_for_verification(findings, verify_severities, security_sensitive_pattern, **kwargs)


def test_matches_real_jinja_baseline_plus_axis_1_and_axis_4_fields():
    assert _prepare(BASELINE_FIXTURE_FINDINGS, id_factory=_fixed_id_factory()) == BASELINE_RESULT


def test_defaults_only_fill_missing_keys():
    # A finding that already has evidence_status/requires_execution keeps
    # its own values -- combine()'s semantics, the finding's own keys win
    # over the defaults dict, not the other way around.
    result = _prepare(BASELINE_FIXTURE_FINDINGS)
    assert result[1]["evidence_status"] == "already_set"
    assert result[1]["requires_execution"] is True


def test_defaults_apply_when_keys_absent():
    result = _prepare(BASELINE_FIXTURE_FINDINGS)
    assert result[0]["evidence_status"] is None
    assert result[0]["requires_execution"] is False
    assert result[0]["severity_status"] is None
    assert result[0]["original_severity"] is None
    assert result[0]["used_static_reachability_trace"] is False


def test_used_static_reachability_trace_default_does_not_override_an_existing_value():
    findings = [{"file": "a.py", "severity": "Nit", "used_static_reachability_trace": True}]
    result = _prepare(findings)
    assert result[0]["used_static_reachability_trace"] is True


def test_verify_index_is_positional_not_content_derived():
    findings = [{"file": "z.py", "line": 1, "severity": "Nit"}, {"file": "a.py", "line": 1, "severity": "Nit"}]
    result = _prepare(findings)
    assert [f["_verify_index"] for f in result] == [0, 1]
    assert [f["file"] for f in result] == ["z.py", "a.py"]  # order preserved, not re-sorted


def test_empty_list_returns_empty_list():
    assert _prepare([]) == []


def test_a_stray_existing_verify_index_is_overwritten_with_a_fresh_one():
    findings = [{"file": "a.py", "severity": "Nit", "_verify_index": 999}]
    result = _prepare(findings)
    assert result[0]["_verify_index"] == 0


# --- _verify_eligible tagging (axis 1 scope decision) ------------------


def test_critical_and_major_are_always_eligible():
    findings = [
        {"file": "a.py", "severity": "Critical", "lens": "Quality"},
        {"file": "a.py", "severity": "Major", "lens": "Quality"},
    ]
    result = _prepare(findings)
    assert [f["_verify_eligible"] for f in result] == [True, True]


def test_plain_minor_and_nit_are_not_eligible():
    findings = [
        {"file": "a.py", "severity": "Minor", "lens": "Quality", "description": "", "evidence": ""},
        {"file": "a.py", "severity": "Nit", "lens": "Security", "description": "", "evidence": ""},
    ]
    result = _prepare(findings)
    assert [f["_verify_eligible"] for f in result] == [False, False]


def test_minor_security_lens_finding_is_eligible():
    # Heuristic borderline signal #1: raised by the Security lens at all,
    # regardless of file/evidence content.
    findings = [{"file": "unrelated.py", "severity": "Minor", "lens": "Security", "description": "", "evidence": ""}]
    result = _prepare(findings)
    assert result[0]["_verify_eligible"] is True


def test_minor_finding_touching_security_sensitive_pattern_is_eligible():
    # Heuristic borderline signal #2: a non-Security-lens Minor finding
    # whose file/description/evidence matches the security-sensitive
    # keyword pattern.
    findings = [
        {
            "file": "auth/token_helper.py", "severity": "Minor", "lens": "Functionality",
            "description": "credential handling looks off", "evidence": "",
        }
    ]
    result = _prepare(findings)
    assert result[0]["_verify_eligible"] is True


def test_minor_finding_with_no_signal_at_all_is_not_eligible():
    findings = [
        {
            "file": "docs/readme_helper.py", "severity": "Minor", "lens": "Quality",
            "description": "typo in a comment", "evidence": "teh instead of the",
        }
    ]
    result = _prepare(findings)
    assert result[0]["_verify_eligible"] is False


def test_security_sensitive_pattern_matches_case_insensitively():
    findings = [{"file": "a.py", "severity": "Minor", "lens": "Quality", "description": "SSH key handling", "evidence": ""}]
    result = _prepare(findings)
    assert result[0]["_verify_eligible"] is True


def test_haystack_is_truncated_before_matching():
    # file/description/evidence trace back to diff content -- bounds
    # the regex engine's worst-case input size regardless of pattern
    # complexity. A match past the truncation point must NOT fire.
    padding = "x" * 10_000
    findings = [
        {"file": "a.py", "severity": "Minor", "lens": "Quality", "description": padding, "evidence": "credential"}
    ]
    result = _prepare(findings)
    assert result[0]["_verify_eligible"] is False


def test_haystack_truncation_does_not_affect_an_early_match():
    findings = [
        {"file": "a.py", "severity": "Minor", "lens": "Quality", "description": "credential handling", "evidence": "x" * 10_000}
    ]
    result = _prepare(findings)
    assert result[0]["_verify_eligible"] is True


def test_invalid_security_sensitive_pattern_raises_a_clear_error():
    # The pattern is operator-configured: a syntax error (e.g. an
    # unbalanced group) should fail with a clear message naming the
    # pattern, not a raw re.error traceback.
    findings = [{"file": "a.py", "severity": "Minor", "lens": "Quality", "description": "", "evidence": ""}]
    with pytest.raises(InvalidSecurityPatternError, match=r"\(unbalanced"):
        _prepare(findings, security_sensitive_pattern="(unbalanced")


# --- finding_id assignment (implement-continues-finding-id) -----------


def test_eligible_findings_get_a_real_finding_id():
    findings = [{"file": "a.py", "line": 1, "severity": "Critical", "lens": "Quality"}]
    result = _prepare(findings)
    assert isinstance(result[0]["finding_id"], str) and result[0]["finding_id"]


def test_ineligible_findings_keep_finding_id_none():
    findings = [{"file": "a.py", "line": 1, "severity": "Nit", "lens": "Quality"}]
    result = _prepare(findings)
    assert result[0]["finding_id"] is None


def test_every_eligible_finding_gets_a_distinct_id():
    findings = [
        {"file": "a.py", "line": 1, "severity": "Critical", "lens": "Quality"},
        {"file": "b.py", "line": 2, "severity": "Major", "lens": "Quality"},
    ]
    result = _prepare(findings)
    assert result[0]["finding_id"] != result[1]["finding_id"]


def test_a_stray_existing_finding_id_is_overwritten_with_a_fresh_one():
    findings = [{"file": "a.py", "line": 1, "severity": "Critical", "lens": "Quality", "finding_id": "stale-id"}]
    result = _prepare(findings, id_factory=_fixed_id_factory())
    assert result[0]["finding_id"] == "id-0"


def test_continuity_fields_default_to_none_regardless_of_eligibility():
    findings = [
        {"file": "a.py", "line": 1, "severity": "Critical", "lens": "Quality"},
        {"file": "b.py", "line": 2, "severity": "Nit", "lens": "Quality"},
    ]
    result = _prepare(findings)
    for f in result:
        assert f["continues_finding_id"] is None
        assert f["continuity_status"] is None
        assert f["continuity_rationale"] is None


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "prepare_findings_for_verification"
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


def _without_finding_id(findings):
    return [{k: v for k, v in f.items() if k != "finding_id"} for f in findings]


def test_action_module_run_matches_pure_function():
    # finding_id compared separately (shape only, not value): the
    # ActionModule and this test's own _prepare() call each assign an
    # independent real uuid4, so they can never be equal by chance.
    args = {
        "findings": BASELINE_FIXTURE_FINDINGS,
        "verify_severities": VERIFY_SEVERITIES,
        "security_sensitive_pattern": SECURITY_PATTERN,
    }
    result = _run_action_module(args)
    assert "failed" not in result
    assert _without_finding_id(result["findings"]) == _without_finding_id(_prepare(BASELINE_FIXTURE_FINDINGS))
    assert isinstance(result["findings"][0]["finding_id"], str) and result["findings"][0]["finding_id"]
    assert result["findings"][1]["finding_id"] is None


def test_action_module_requires_all_three_args():
    result = _run_action_module({"findings": []})
    assert result["failed"] is True


def test_action_module_treats_empty_findings_list_as_present_not_missing():
    result = _run_action_module(
        {"findings": [], "verify_severities": VERIFY_SEVERITIES, "security_sensitive_pattern": SECURITY_PATTERN}
    )
    assert "failed" not in result
    assert result["findings"] == []


def test_action_module_fails_clearly_on_invalid_security_sensitive_pattern():
    result = _run_action_module(
        {"findings": [], "verify_severities": VERIFY_SEVERITIES, "security_sensitive_pattern": "(unbalanced"}
    )
    assert result["failed"] is True
    assert "(unbalanced" in result["msg"]
