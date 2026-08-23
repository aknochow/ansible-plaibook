# -*- coding: utf-8 -*-
"""Behavioral tests for resolve_continuity_claim.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

No prior Jinja equivalent exists -- new work for
implement-continues-finding-id (handoff.ansible-plaibook-stable-finding-id.yaml).
"""
from __future__ import annotations

from resolve_continuity_claim import ActionModule, resolve_continuity_claim

PRIOR_FINDINGS = [
    {"finding_id": "prior-1", "file": "a.py", "line": 10, "description": "reversed args"},
    {"finding_id": "prior-2", "file": "b.py", "line": 20, "description": "missing null check"},
]


def test_no_claim_never_audits():
    result = resolve_continuity_claim({"continues_finding_id": None}, PRIOR_FINDINGS)
    assert result == {"should_audit": False, "claimed_prior_finding": None}


def test_empty_claim_input_never_audits():
    # {} (key absent entirely) is the fallback shape used on a stall/
    # turn-exhaustion path -- dict.get() treats it identically to an
    # explicit None value.
    result = resolve_continuity_claim({}, PRIOR_FINDINGS)
    assert result == {"should_audit": False, "claimed_prior_finding": None}


def test_matching_claim_id_triggers_audit_with_the_right_finding():
    result = resolve_continuity_claim({"continues_finding_id": "prior-2"}, PRIOR_FINDINGS)
    assert result["should_audit"] is True
    assert result["claimed_prior_finding"] == PRIOR_FINDINGS[1]


def test_hallucinated_claim_id_never_audits():
    # No real prior finding to show an auditor -- skip the call rather
    # than run it against a fabricated comparison target.
    result = resolve_continuity_claim({"continues_finding_id": "made-up-id"}, PRIOR_FINDINGS)
    assert result == {"should_audit": False, "claimed_prior_finding": None}


def test_empty_prior_round_findings_never_audits():
    result = resolve_continuity_claim({"continues_finding_id": "prior-1"}, [])
    assert result == {"should_audit": False, "claimed_prior_finding": None}


def test_first_match_wins_if_ids_somehow_collide():
    # Shouldn't happen in real use (finding_id is a fresh uuid every
    # round), but a deterministic pick beats an unhandled ambiguity.
    duplicated = [PRIOR_FINDINGS[0], {**PRIOR_FINDINGS[0], "description": "a different description"}]
    result = resolve_continuity_claim({"continues_finding_id": "prior-1"}, duplicated)
    assert result["claimed_prior_finding"] == duplicated[0]


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "resolve_continuity_claim"
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


def test_action_module_run_matches_pure_function():
    args = {"claim_input": {"continues_finding_id": "prior-2"}, "prior_round_findings": PRIOR_FINDINGS}
    result = _run_action_module(args)
    assert "failed" not in result
    assert result["should_audit"] is True
    assert result["claimed_prior_finding"] == PRIOR_FINDINGS[1]


def test_action_module_treats_empty_claim_input_as_present_not_missing():
    result = _run_action_module({"claim_input": {}, "prior_round_findings": []})
    assert "failed" not in result
    assert result["should_audit"] is False


def test_action_module_treats_empty_prior_round_findings_as_present_not_missing():
    result = _run_action_module({"claim_input": {"continues_finding_id": "x"}, "prior_round_findings": []})
    assert "failed" not in result
    assert result["should_audit"] is False


def test_action_module_requires_both_args():
    result = _run_action_module({"claim_input": {}})
    assert result["failed"] is True
    assert "prior_round_findings" in result["msg"]
