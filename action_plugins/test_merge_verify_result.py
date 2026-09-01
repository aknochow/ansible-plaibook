# -*- coding: utf-8 -*-
"""Behavioral tests for merge_verify_result.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

BASELINE_RESULT matches the real verify_finding.yml Jinja expression
this plugin replaces:

    {{ (item | combine({
         'evidence_status': verify_result_status,
         'verification_evidence': verify_result_evidence,
         'verification_rationale': verify_result_rationale,
         'requires_execution': verify_result_requires_execution
       }))
       if item._verify_index == verify_target_finding._verify_index
       else item }}

suggested_severity/original_severity/severity_status/reachability/
trust_boundary/worst_outcome_category/silent_failure and
used_static_reachability_trace are later additions with no Jinja
equivalent to match against.
"""
from __future__ import annotations

import pytest
from merge_verify_result import ActionModule, FindingNotFoundError, merge_verify_result

BASELINE_FIXTURE_FINDINGS = [
    {
        "evidence_status": None, "requires_execution": False,
        "file": "a.py", "line": 10, "severity": "Major", "lens": "Security", "evidence": "e1",
        "_verify_index": 0, "finding_id": "own-id-0",
    },
    {
        "evidence_status": None, "requires_execution": False,
        "file": "b.py", "line": 20, "severity": "Minor", "lens": "Quality", "evidence": "e2",
        "_verify_index": 1, "finding_id": "own-id-1",
    },
]

# No continuity claimed by default. claim_input/audit_result are always
# at least an empty dict, a whole-dict arg shape rather than individual
# nullable scalars.
_NO_CONTINUITY_ARGS = {"claim_input": {}, "audit_result": {}}

# Chosen so suggested_severity equals the finding's own original
# severity ('Minor'), i.e. not disputed, so tests that only care about
# the evidence_status/requires_execution baseline aren't also asserting
# severity-dispute behavior incidentally.
_UNDISPUTED_MINOR_AXIS1_ARGS = {
    "suggested_severity": "Minor",
    "reachability": "realistic",
    "trust_boundary": "crosses-trust-boundary",
    "worst_outcome_category": "availability-degradation",
    "silent_failure": False,
}

# Most tests below don't care about this field, so a single shared
# default keeps their call sites from having to repeat it.
_AXIS4_ARGS = {"used_static_reachability_trace": False}

BASELINE_RESULT = [
    {
        "evidence_status": None, "requires_execution": False,
        "file": "a.py", "line": 10, "severity": "Major", "lens": "Security", "evidence": "e1",
        "_verify_index": 0, "finding_id": "own-id-0",
    },
    {
        "evidence_status": "refuted", "requires_execution": False,
        "file": "b.py", "line": 20, "severity": "Minor", "lens": "Quality", "evidence": "e2",
        "_verify_index": 1,
        "verification_evidence": "checked the source, claim is false",
        "verification_rationale": "the function signature does not match",
        "original_severity": "Minor", "severity_status": "confirmed",
        "reachability": "realistic", "trust_boundary": "crosses-trust-boundary",
        "worst_outcome_category": "availability-degradation", "silent_failure": False,
        "used_static_reachability_trace": False,
        "finding_id": "own-id-1", "continues_finding_id": None,
        "continuity_status": "not-claimed", "continuity_rationale": "",
    },
]


def _merge(
    verify_index,
    evidence_status,
    verification_evidence,
    verification_rationale,
    requires_execution,
    continues_finding_id=None,
    continuity_audit_status=None,
    continuity_audit_rationale="",
    **kwargs,
):
    # audit_result is {} whenever continuity_audit_status is None,
    # matching "no audit ran" regardless of any rationale text passed.
    claim_input = {} if continues_finding_id is None else {"continues_finding_id": continues_finding_id}
    audit_result = (
        {} if continuity_audit_status is None else {"verdict": continuity_audit_status, "rationale": continuity_audit_rationale}
    )
    args = dict(_UNDISPUTED_MINOR_AXIS1_ARGS)
    args.update(_AXIS4_ARGS)
    args.update(kwargs)
    return merge_verify_result(
        BASELINE_FIXTURE_FINDINGS,
        verify_index=verify_index,
        evidence_status=evidence_status,
        verification_evidence=verification_evidence,
        verification_rationale=verification_rationale,
        requires_execution=requires_execution,
        claim_input=claim_input,
        audit_result=audit_result,
        **args,
    )


def test_matches_real_jinja_baseline_plus_axis_1_and_axis_4_fields():
    result = _merge(1, "refuted", "checked the source, claim is false", "the function signature does not match", False)
    assert result == BASELINE_RESULT


def test_only_the_matching_index_is_modified():
    result = _merge(1, "verified", "e", "r", True)
    assert result[0] == BASELINE_FIXTURE_FINDINGS[0]  # untouched, same content


def test_order_and_length_preserved():
    result = _merge(0, "inconclusive", "", "couldn't reach a verdict", False, suggested_severity="Major")
    assert len(result) == len(BASELINE_FIXTURE_FINDINGS)
    assert [f["file"] for f in result] == [f["file"] for f in BASELINE_FIXTURE_FINDINGS]


def test_requires_execution_false_is_a_valid_merge_value_not_missing():
    # False is a legitimate value, not "not provided" -- must not be
    # rejected by an is-falsy arg check.
    result = _merge(0, "refuted", "e", "r", False, suggested_severity="Major")
    assert result[0]["requires_execution"] is False


def test_stringified_verify_index_still_matches_real_ansible_gotcha():
    # verify_index can arrive as the string "1" over a dot-access
    # template rather than the int 1. Findings' own _verify_index stays
    # a native int (never round-tripped through templating).
    result = _merge("1", "refuted", "checked the source, claim is false", "the function signature does not match", False)
    assert result == BASELINE_RESULT


def test_no_matching_index_raises_instead_of_silently_no_opping():
    with pytest.raises(FindingNotFoundError, match="99"):
        _merge(99, "refuted", "e", "r", False)


# --- severity dispute: overwrites `severity`, preserves original -----


def test_undisputed_severity_keeps_the_original_value_and_is_marked_confirmed():
    result = _merge(1, "verified", "e", "r", False, suggested_severity="Minor")
    assert result[1]["severity"] == "Minor"
    assert result[1]["original_severity"] == "Minor"
    assert result[1]["severity_status"] == "confirmed"


def test_disputed_severity_overwrites_severity_but_keeps_original_visible():
    # Finding at index 1 was originally Minor; suggested_severity of
    # Critical models a truly-Critical issue misclassified down.
    result = _merge(1, "verified", "e", "r", False, suggested_severity="Critical")
    assert result[1]["severity"] == "Critical"
    assert result[1]["original_severity"] == "Minor"
    assert result[1]["severity_status"] == "disputed"


def test_disputed_severity_can_downgrade_too():
    result = _merge(0, "verified", "e", "r", False, suggested_severity="Nit")
    assert result[0]["severity"] == "Nit"
    assert result[0]["original_severity"] == "Major"
    assert result[0]["severity_status"] == "disputed"


def test_untouched_findings_keep_their_original_severity_unmodified():
    result = _merge(1, "verified", "e", "r", False, suggested_severity="Critical")
    assert result[0] == BASELINE_FIXTURE_FINDINGS[0]
    assert "original_severity" not in result[0]


def test_axis1_raw_subanswers_are_all_merged_in():
    result = _merge(
        1, "verified", "e", "r", False,
        suggested_severity="Minor",
        reachability="theoretical-only",
        trust_boundary="stays-within-trusted-input",
        worst_outcome_category="crash-dos",
        silent_failure=True,
    )
    assert result[1]["reachability"] == "theoretical-only"
    assert result[1]["trust_boundary"] == "stays-within-trusted-input"
    assert result[1]["worst_outcome_category"] == "crash-dos"
    assert result[1]["silent_failure"] is True


# --- defensive string coercion, matching compute_suggested_severity.py ---


def test_stringified_silent_failure_false_persists_as_a_real_bool():
    result = _merge(1, "verified", "e", "r", False, suggested_severity="Minor", silent_failure="False")
    assert result[1]["silent_failure"] is False


def test_stringified_silent_failure_true_persists_as_a_real_bool():
    result = _merge(1, "verified", "e", "r", False, suggested_severity="Minor", silent_failure="True")
    assert result[1]["silent_failure"] is True


# --- continuity resolution (implement-continues-finding-id) -----------


def test_no_claim_keeps_own_finding_id_and_marks_not_claimed():
    result = _merge(1, "verified", "e", "r", False, suggested_severity="Minor")
    assert result[1]["finding_id"] == "own-id-1"
    assert result[1]["continues_finding_id"] is None
    assert result[1]["continuity_status"] == "not-claimed"
    assert result[1]["continuity_rationale"] == ""


def test_plausible_audit_inherits_the_claimed_id_and_marks_confirmed():
    # A real-shaped id (uuid4().hex[:12]-like), not an arbitrary string.
    result = _merge(
        1, "verified", "e", "r", False, suggested_severity="Minor",
        continues_finding_id="a1b2c3d4e5f6",
        continuity_audit_status="plausible",
        continuity_audit_rationale="same guard clause, same file/line across rounds",
    )
    assert result[1]["finding_id"] == "a1b2c3d4e5f6"
    assert result[1]["continues_finding_id"] == "a1b2c3d4e5f6"
    assert result[1]["continuity_status"] == "confirmed"
    assert result[1]["continuity_rationale"] == "same guard clause, same file/line across rounds"


def test_plausible_audit_with_a_malformed_id_is_refuted_not_trusted():
    # Defensive backstop: resolve_continuity_claim.py already exact-
    # matches continues_finding_id against a real prior-round finding_id
    # before the audit runs, so this shouldn't be reachable in practice,
    # but the safety property should be enforced here directly rather
    # than depend on a different function's own matching.
    result = _merge(
        1, "verified", "e", "r", False, suggested_severity="Minor",
        continues_finding_id="not-a-valid-hex-id",
        continuity_audit_status="plausible",
        continuity_audit_rationale="claims to be plausible anyway",
    )
    assert result[1]["finding_id"] == "own-id-1"
    assert result[1]["continues_finding_id"] == "not-a-valid-hex-id"
    assert result[1]["continuity_status"] == "refuted"


def test_implausible_audit_keeps_own_id_but_records_the_claim_and_marks_refuted():
    result = _merge(
        1, "verified", "e", "r", False, suggested_severity="Minor",
        continues_finding_id="prior-id-42",
        continuity_audit_status="implausible",
        continuity_audit_rationale="different function, unrelated concern",
    )
    assert result[1]["finding_id"] == "own-id-1"
    assert result[1]["continues_finding_id"] == "prior-id-42"
    assert result[1]["continuity_status"] == "refuted"
    assert result[1]["continuity_rationale"] == "different function, unrelated concern"


def test_unexpected_audit_status_value_is_treated_as_refuted_not_trusted():
    # Shouldn't happen in real use (verify_finding.yml only runs the
    # audit when a claim exists, and the audit schema only allows
    # plausible/implausible) -- but an unexpected value must not be
    # silently treated as confirmation.
    result = _merge(
        1, "verified", "e", "r", False, suggested_severity="Minor",
        continues_finding_id="prior-id-42",
        continuity_audit_status="something-unexpected",
        continuity_audit_rationale="",
    )
    assert result[1]["finding_id"] == "own-id-1"
    assert result[1]["continuity_status"] == "refuted"


# --- used_static_reachability_trace (axis 4) --------------------------


def test_used_static_reachability_trace_false_is_a_valid_merge_value_not_missing():
    # Same is-falsy-arg-check risk as requires_execution above, for this
    # field -- False must be accepted, not treated as "not provided".
    result = _merge(0, "refuted", "e", "r", False, suggested_severity="Major")
    assert result[0]["used_static_reachability_trace"] is False


def test_used_static_reachability_trace_true_is_merged_in():
    result = _merge(1, "verified", "e", "r", False, suggested_severity="Minor", used_static_reachability_trace=True)
    assert result[1]["used_static_reachability_trace"] is True


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "merge_verify_result"
        self.async_val = False
        self.check_mode = False


_FULL_ARGS = {
    "findings": BASELINE_FIXTURE_FINDINGS,
    "verify_index": 1,
    "evidence_status": "refuted",
    "verification_evidence": "checked the source, claim is false",
    "verification_rationale": "the function signature does not match",
    "requires_execution": False,
    **_UNDISPUTED_MINOR_AXIS1_ARGS,
    **_AXIS4_ARGS,
    **_NO_CONTINUITY_ARGS,
}


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


def test_action_module_run_matches_pure_function():
    result = _run_action_module(_FULL_ARGS)
    assert "failed" not in result
    assert result["findings"] == BASELINE_RESULT


@pytest.mark.parametrize("missing_arg", sorted(_FULL_ARGS))
def test_action_module_requires_every_arg(missing_arg):
    args = dict(_FULL_ARGS)
    del args[missing_arg]
    result = _run_action_module(args)
    assert result["failed"] is True
    assert missing_arg in result["msg"]


def test_action_module_accepts_requires_execution_false_without_flagging_it_missing():
    args = dict(_FULL_ARGS)
    args["requires_execution"] = False
    result = _run_action_module(args)
    assert "failed" not in result


def test_action_module_accepts_silent_failure_false_without_flagging_it_missing():
    args = dict(_FULL_ARGS)
    args["silent_failure"] = False
    result = _run_action_module(args)
    assert "failed" not in result


def test_action_module_accepts_used_static_reachability_trace_false_without_flagging_it_missing():
    args = dict(_FULL_ARGS)
    args["used_static_reachability_trace"] = False
    result = _run_action_module(args)
    assert "failed" not in result
