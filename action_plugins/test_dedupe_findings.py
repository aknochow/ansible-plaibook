# -*- coding: utf-8 -*-
"""Behavioral tests for dedupe_findings.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

The fixture exercises three dedup behaviors:
1. Byte-identical evidence at DIFFERENT lines, different lens
   categories: must dedupe via the evidence pass (not the file:line
   pass), keeping the higher severity.
2. Same code, different internal indentation (tabs vs. spaces):
   whitespace-collapse must recognize these as the same evidence.
3. Case-insensitive dedup_key matching: "SANDBOX.py:200" and
   "sandbox.py:200" dedupe as the same key (see dedupe_findings.py's
   own module docstring for why this is intentional, not incidental).

Order is not asserted against a specific baseline: compared via
set-of-fingerprints instead of list equality (see dedupe_findings.py's
module docstring for why insertion order is a disclosed, intentional
change from the legacy behavior).
"""
from __future__ import annotations

import pytest

from dedupe_findings import ActionModule, InvalidFindingError, _collapse_whitespace, dedupe_findings

SEVERITY_POINTS = {"Critical": 20, "Major": 10, "Minor": 5, "Nit": 0}

# Security findings first, then review findings, matching merge.yml's
# "Combine findings from both agents" task order.
BASELINE_FIXTURE_FINDINGS = [
    # security_result
    {
        "file": "sandbox.py", "line": 40, "severity": "Major", "lens": "Security",
        "evidence": "client.create(name=name)", "fix": "Validate name before use",
        "description": "possible issue A",
    },
    {
        "file": "sandbox.py", "line": 100, "severity": "Minor", "lens": "Security",
        "evidence": "if\tcondition:\n\t\tdo_thing()", "fix": "minor note",
        "description": "possible issue B",
    },
    {
        "file": "SANDBOX.py", "line": 200, "severity": "Critical", "lens": "Security",
        "evidence": "unrelated evidence for the case probe", "fix": "fix C",
        "description": "case probe upper",
    },
    # review_result
    {
        "file": "sandbox.py", "line": 42, "severity": "Minor", "lens": "Functionality",
        "evidence": "client.create(name=name)", "fix": "different fix text",
        "description": "possible issue A duplicate",
    },
    {
        "file": "sandbox.py", "line": 101, "severity": "Major", "lens": "Functionality",
        "evidence": "if   condition:\n\t\t\tdo_thing()", "fix": "a different fix for the same issue",
        "description": "possible issue B duplicate, higher severity",
    },
    {
        "file": "sandbox.py", "line": 200, "severity": "Major", "lens": "Functionality",
        "evidence": "different evidence text so evidence-key doesn't also collide", "fix": "fix D",
        "description": "case probe lower",
    },
    {
        "file": "other_file.py", "line": 5, "severity": "Nit", "lens": "Quality",
        "evidence": "totally unrelated", "fix": "trivial",
        "description": "unique survivor",
    },
]

# Identified by `description`, since it's unique per input finding here
# and easier to read than full dict equality in a fixture table.
BASELINE_SURVIVOR_DESCRIPTIONS = {
    "unique survivor",
    "possible issue A",  # NOT "possible issue A duplicate" -- Major beat Minor
    "possible issue B duplicate, higher severity",  # Major beat Minor
    "case probe upper",  # Critical beat Major -- confirms case-insensitive dedup_key match
}


def test_matches_baseline_survivor_set():
    result = dedupe_findings(BASELINE_FIXTURE_FINDINGS, SEVERITY_POINTS)
    assert {finding["description"] for finding in result} == BASELINE_SURVIVOR_DESCRIPTIONS
    assert len(result) == 4


def test_case_insensitive_dedup_key_matches_real_jinja_behavior():
    # Isolated from the full fixture: SANDBOX.py:200 (Critical) and
    # sandbox.py:200 (Major) must dedupe as the same file:line key.
    findings = [
        {"file": "SANDBOX.py", "line": 200, "severity": "Critical", "evidence": "e1", "lens": "Security"},
        {"file": "sandbox.py", "line": 200, "severity": "Major", "evidence": "e2", "lens": "Functionality"},
    ]
    result = dedupe_findings(findings, SEVERITY_POINTS)
    assert len(result) == 1
    assert result[0]["severity"] == "Critical"


def test_historical_bug_1_byte_identical_evidence_different_lines_and_lenses():
    findings = [
        {"file": "a.py", "line": 40, "severity": "Major", "evidence": "client.create(name=name)", "lens": "Security"},
        {"file": "a.py", "line": 42, "severity": "Minor", "evidence": "client.create(name=name)", "lens": "Functionality"},
    ]
    result = dedupe_findings(findings, SEVERITY_POINTS)
    assert len(result) == 1
    assert result[0]["severity"] == "Major"


def test_historical_bug_2_different_internal_indentation_same_evidence():
    findings = [
        {"file": "a.py", "line": 100, "severity": "Minor", "evidence": "if\tcondition:\n\t\tdo_thing()", "lens": "Security"},
        {"file": "a.py", "line": 101, "severity": "Major", "evidence": "if   condition:\n\t\t\tdo_thing()", "lens": "Functionality"},
    ]
    result = dedupe_findings(findings, SEVERITY_POINTS)
    assert len(result) == 1
    assert result[0]["severity"] == "Major"


def test_naive_trim_would_have_missed_leading_trailing_whitespace_variant():
    # A `| trim`-only collapse would treat these as different keys;
    # whitespace-run collapsing must treat them as the same.
    assert _collapse_whitespace("  a   b\tc  ") == "a b c"


def test_dedup_key_output_omits_internal_bookkeeping_fields():
    # dedup_key/evidence_dedup_key/severity_rank are internal
    # bookkeeping, never part of the findings schema, never read
    # downstream.
    findings = [{"file": "a.py", "line": 1, "severity": "Nit", "evidence": "e", "lens": "Quality"}]
    result = dedupe_findings(findings, SEVERITY_POINTS)
    assert set(result[0].keys()) == {"file", "line", "severity", "evidence", "lens"}


@pytest.mark.parametrize("missing_key", ["file", "line", "severity", "evidence"])
def test_missing_required_key_raises_instead_of_keyerror(missing_key):
    finding = {"file": "a.py", "line": 1, "severity": "Minor", "evidence": "e"}
    del finding[missing_key]
    with pytest.raises(InvalidFindingError, match=f"no '{missing_key}' key"):
        dedupe_findings([finding], SEVERITY_POINTS)


def test_unknown_severity_raises_instead_of_keyerror():
    finding = {"file": "a.py", "line": 1, "severity": "critical", "evidence": "e"}  # lowercase, not in the enum
    with pytest.raises(InvalidFindingError, match="critical"):
        dedupe_findings([finding], SEVERITY_POINTS)


def test_order_is_first_occurrence_not_baseline_incidental_order():
    findings = [
        {"file": "z.py", "line": 1, "severity": "Nit", "evidence": "e1", "lens": "Quality"},
        {"file": "a.py", "line": 1, "severity": "Nit", "evidence": "e2", "lens": "Quality"},
    ]
    result = dedupe_findings(findings, SEVERITY_POINTS)
    assert [f["file"] for f in result] == ["z.py", "a.py"]


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "dedupe_findings"
        self.async_val = False
        self.check_mode = False


def _run_action_module(findings, severity_points):
    action = ActionModule(
        task=_FakeTask({"findings": findings, "severity_points": severity_points}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(BASELINE_FIXTURE_FINDINGS, SEVERITY_POINTS)
    assert "failed" not in result
    assert result["findings"] == dedupe_findings(BASELINE_FIXTURE_FINDINGS, SEVERITY_POINTS)


def test_action_module_requires_both_args():
    action = ActionModule(
        task=_FakeTask({"findings": []}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
