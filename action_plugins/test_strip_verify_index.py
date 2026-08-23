# -*- coding: utf-8 -*-
"""Behavioral tests for strip_verify_index.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

BASELINE_RESULT matches the real verify.yml Jinja expression this
plugin replaces:

    {{ findings | map('dict2items') | map('rejectattr', 'key', 'equalto', '_verify_index')
       | map('items2dict') | list }}
"""
from __future__ import annotations

from strip_verify_index import ActionModule, strip_verify_index

BASELINE_FIXTURE_FINDINGS = [
    {
        "evidence_status": None, "requires_execution": False,
        "file": "a.py", "line": 10, "severity": "Major", "lens": "Security", "evidence": "e1",
        "_verify_index": 0,
    },
    {
        "evidence_status": "refuted", "requires_execution": False,
        "file": "b.py", "line": 20, "severity": "Minor", "lens": "Quality", "evidence": "e2",
        "_verify_index": 1,
        "verification_evidence": "checked the source, claim is false",
        "verification_rationale": "the function signature does not match",
    },
]

# Captured via the real Templar render described above.
BASELINE_RESULT = [
    {
        "evidence_status": None, "requires_execution": False,
        "file": "a.py", "line": 10, "severity": "Major", "lens": "Security", "evidence": "e1",
    },
    {
        "evidence_status": "refuted", "requires_execution": False,
        "file": "b.py", "line": 20, "severity": "Minor", "lens": "Quality", "evidence": "e2",
        "verification_evidence": "checked the source, claim is false",
        "verification_rationale": "the function signature does not match",
    },
]


def test_matches_real_jinja_baseline():
    assert strip_verify_index(BASELINE_FIXTURE_FINDINGS) == BASELINE_RESULT


def test_verify_index_key_is_gone():
    result = strip_verify_index(BASELINE_FIXTURE_FINDINGS)
    assert all("_verify_index" not in finding for finding in result)


def test_other_keys_survive_untouched():
    result = strip_verify_index(BASELINE_FIXTURE_FINDINGS)
    assert result[1]["evidence_status"] == "refuted"
    assert result[1]["verification_evidence"] == "checked the source, claim is false"


def test_verify_eligible_is_also_stripped():
    findings = [{"file": "a.py", "severity": "Minor", "_verify_index": 0, "_verify_eligible": True}]
    result = strip_verify_index(findings)
    assert result == [{"file": "a.py", "severity": "Minor"}]


def test_a_finding_never_indexed_is_left_alone():
    # explore.yml's own fold-in step never adds _verify_index (only
    # verify.yml's prepare pass does) -- stripping a finding that never
    # had the key must not raise.
    findings = [{"file": "a.py", "line": 1}]
    assert strip_verify_index(findings) == findings


def test_empty_list_returns_empty_list():
    assert strip_verify_index([]) == []


def test_order_and_length_preserved():
    result = strip_verify_index(BASELINE_FIXTURE_FINDINGS)
    assert len(result) == len(BASELINE_FIXTURE_FINDINGS)
    assert [f["file"] for f in result] == [f["file"] for f in BASELINE_FIXTURE_FINDINGS]


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "strip_verify_index"
        self.async_val = False
        self.check_mode = False


def _run_action_module(findings):
    action = ActionModule(
        task=_FakeTask({"findings": findings}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(BASELINE_FIXTURE_FINDINGS)
    assert "failed" not in result
    assert result["findings"] == strip_verify_index(BASELINE_FIXTURE_FINDINGS)


def test_action_module_requires_findings_arg():
    action = ActionModule(
        task=_FakeTask({}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
