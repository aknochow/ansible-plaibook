# -*- coding: utf-8 -*-
"""Behavioral tests for compute_review_scores.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

This is NOT a port of an existing Jinja implementation -- the 0-100
scale formula never existed as Jinja anywhere in this repo (built
directly against the new scale per handoff.ansible-plaibook-100-point-
scoring-scale.yaml). So the equivalence oracle here is a hand-written
reference Jinja expression matching the NEW spec exactly (max(10, 100 -
sum(...)), mean of three lens scores rounded to 1 decimal, verdict from
severity presence), rendered via Ansible's real Templar -- same
methodology as filter_self_refuted_findings.py's equivalence tests, just
proving "does this Python correctly implement the new spec" rather than
"does this Python match a prior Jinja implementation."
"""
from __future__ import annotations

import pytest
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from compute_review_scores import ActionModule, InvalidFindingError, compute_scores_and_verdict

SEVERITY_POINTS = {"Critical": 20, "Major": 10, "Minor": 5, "Nit": 0}

# Reference Jinja expressions for the NEW 0-100 spec, hand-written from
# handoff.ansible-plaibook-100-point-scoring-scale.yaml's confirmed formula --
# NOT copied from any existing file, since none exists at this scale yet.
_LENS_SCORE_EXPR = (
    "{{ [10, 100 - (findings | selectattr('lens', 'equalto', '%s')"
    " | map(attribute='severity') | map('extract', severity_points)"
    " | list | sum)] | max | float }}"
)
_OVERALL_EXPR = (
    "{{ (((score_functionality | float) + (score_security | float)"
    " + (score_quality | float)) / 3) | round(1) }}"
)
_VERDICT_EXPR = (
    "{{ 'NEEDS_CHANGES' if (findings | selectattr('severity', 'in', ['Critical', 'Major'])"
    " | list | length > 0) else 'READY_FOR_HUMAN_REVIEW' }}"
)


def _render_reference_jinja(findings, severity_points):
    # Ansible's non-native Templar (jinja2_native=False, this repo's
    # default -- confirmed via ansible.constants.DEFAULT_JINJA2_NATIVE)
    # returns plain scalar results (int/float) as STRINGS:
    # ansible_eval_concat only literal_eval's a rendered string back to a
    # native type when it looks like a list/dict/bool (starts with '{'/
    # '[' or equals 'True'/'False') -- a bare "100.0" stays a string.
    # This is exactly why the real Jinja formula (and this reference
    # expression, copying it) sprinkles explicit `| float` casts
    # everywhere: every downstream consumer has to re-cast. Cast here too
    # so the comparison reflects what a real caller does, not a mismatch
    # between Python's native float and Jinja's stringified one.
    templar = Templar(loader=DataLoader())
    templar.available_variables = {"findings": findings, "severity_points": severity_points}
    # trust_as_template(): ansible-core 2.21+ added a template-trust check
    # that Templar.template() enforces by silently returning the input
    # STRING UNCHANGED (not raising) when it isn't marked trusted -- a raw
    # Python string literal built in test code is untrusted by default.
    # Confirmed live: without this wrapper, float(templar.template(...))
    # raised ValueError on the literal, unrendered expression text rather
    # than a real score. Real Ansible playbook content (loaded from a .yml
    # file via DataLoader) is trusted automatically; only this kind of
    # in-process literal needs tagging.
    score_functionality = float(templar.template(trust_as_template(_LENS_SCORE_EXPR % "Functionality")))
    score_security = float(templar.template(trust_as_template(_LENS_SCORE_EXPR % "Security")))
    score_quality = float(templar.template(trust_as_template(_LENS_SCORE_EXPR % "Quality")))
    templar.available_variables.update(
        {
            "score_functionality": score_functionality,
            "score_security": score_security,
            "score_quality": score_quality,
        }
    )
    return {
        "score_functionality": score_functionality,
        "score_security": score_security,
        "score_quality": score_quality,
        "score_overall": float(templar.template(trust_as_template(_OVERALL_EXPR))),
        "verdict": templar.template(trust_as_template(_VERDICT_EXPR)),
    }


def _finding(lens, severity, **extra):
    finding = {"lens": lens, "severity": severity, "file": "f.py", "line": 1}
    finding.update(extra)
    return finding


FIXTURES = [
    pytest.param([], id="no-findings-perfect-score"),
    pytest.param([_finding("Security", "Critical")], id="single-critical"),
    pytest.param([_finding("Functionality", "Minor")], id="single-minor"),
    pytest.param(
        [
            _finding("Security", "Critical"),
            _finding("Security", "Major"),
            _finding("Functionality", "Minor"),
            _finding("Quality", "Nit"),
        ],
        id="mixed-across-lenses",
    ),
    pytest.param(
        [_finding("Security", "Critical") for _ in range(10)],
        id="many-criticals-floor-at-10-not-negative",
    ),
    pytest.param(
        [_finding("Security", "Minor", evidence_status="refuted", _verify_index=0)],
        id="finding-with-extra-verify-fields-still-scores",
    ),
]


@pytest.mark.parametrize("findings", FIXTURES)
def test_python_matches_reference_jinja(findings):
    python_result = compute_scores_and_verdict(findings, SEVERITY_POINTS)
    jinja_result = _render_reference_jinja(findings, SEVERITY_POINTS)
    assert python_result == jinja_result


def test_perfect_score_is_100_not_10():
    result = compute_scores_and_verdict([], SEVERITY_POINTS)
    assert result["score_overall"] == 100.0
    assert result["score_functionality"] == 100.0


def test_score_floors_at_10_not_1():
    findings = [_finding("Security", "Critical") for _ in range(10)]
    result = compute_scores_and_verdict(findings, SEVERITY_POINTS)
    assert result["score_security"] == 10.0


def test_verdict_needs_changes_on_major():
    findings = [_finding("Functionality", "Major")]
    result = compute_scores_and_verdict(findings, SEVERITY_POINTS)
    assert result["verdict"] == "NEEDS_CHANGES"


def test_verdict_ready_when_only_minor_and_nit():
    findings = [_finding("Functionality", "Minor"), _finding("Quality", "Nit")]
    result = compute_scores_and_verdict(findings, SEVERITY_POINTS)
    assert result["verdict"] == "READY_FOR_HUMAN_REVIEW"


def test_unknown_severity_raises_instead_of_keyerror():
    # Verified finding from dogfooding this exact plugin: a raw
    # severity_points[finding["severity"]] KeyError would be an opaque
    # crash if a finding's severity ever falls outside the closed set
    # (schema-constrained today, but this codebase doesn't trust model
    # compliance elsewhere either -- see filter_self_refuted_findings.py).
    findings = [_finding("Functionality", "critical")]  # lowercase, not in the enum
    with pytest.raises(InvalidFindingError, match="critical"):
        compute_scores_and_verdict(findings, SEVERITY_POINTS)


def test_missing_severity_key_raises_instead_of_keyerror():
    # Verified finding from dogfooding this exact plugin (MR !11): the
    # unknown-severity check above indexes finding["severity"] in its own
    # set-comprehension, so a finding missing the key ENTIRELY would
    # raise a raw KeyError from that check itself, never reaching the
    # clear message -- a distinct gap from an out-of-enum value.
    findings = [{"lens": "Functionality", "file": "f.py", "line": 1}]  # no "severity" key
    with pytest.raises(InvalidFindingError, match="no 'severity' key"):
        compute_scores_and_verdict(findings, SEVERITY_POINTS)


def test_missing_lens_key_raises_instead_of_keyerror():
    # Same class of gap as the missing-severity-key case above, flagged
    # separately on MR !11's next review round: 'lens' is equally
    # required by the findings schema, so it gets the same guard.
    findings = [{"severity": "Minor", "file": "f.py", "line": 1}]  # no "lens" key
    with pytest.raises(InvalidFindingError, match="no 'lens' key"):
        compute_scores_and_verdict(findings, SEVERITY_POINTS)


def test_rounding_matches_jinja_round_filter():
    # Contrived to land on a value where naive float rounding could
    # diverge (e.g. banker's rounding edge cases) -- confirms Python's
    # round() and Jinja's round(1) filter (which delegates to the same
    # builtin round() under the hood) agree, not just on "nice" values.
    findings = [
        _finding("Functionality", "Minor"),
        _finding("Security", "Minor"),
        _finding("Security", "Minor"),
    ]
    python_result = compute_scores_and_verdict(findings, SEVERITY_POINTS)
    jinja_result = _render_reference_jinja(findings, SEVERITY_POINTS)
    assert python_result["score_overall"] == jinja_result["score_overall"]


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles rather
# than a general ActionBase harness) --------------------------------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "compute_review_scores"
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
    findings = [_finding("Security", "Critical"), _finding("Functionality", "Minor")]
    result = _run_action_module(findings, SEVERITY_POINTS)
    assert "failed" not in result
    expected = compute_scores_and_verdict(findings, SEVERITY_POINTS)
    for key, value in expected.items():
        assert result[key] == value


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


def test_action_module_fails_clearly_on_unknown_severity():
    result = _run_action_module([_finding("Functionality", "critical")], SEVERITY_POINTS)
    assert result["failed"] is True
    assert "critical" in result["msg"]
