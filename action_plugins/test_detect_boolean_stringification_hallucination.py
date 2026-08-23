# -*- coding: utf-8 -*-
"""Behavioral tests for detect_boolean_stringification_hallucination.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

This claim shape (a boolean value claimed to get stringified by Ansible/
Jinja templating, breaking downstream truthiness) recurs across reviews
in different wordings but is consistently false; see
detect_boolean_stringification_hallucination.py's own module docstring
for the underlying reasoning. Three kinds of coverage:
1. A real, confirmed-shipped occurrence, must be detected.
2. Paraphrased variants in other documented wordings, proving the check
   generalizes beyond the one exact case it was built from.
3. Negative controls: findings that superficially share a word or two
   but are not this claim shape, must not match, since this check is
   scoped narrowly on purpose, not a general hallucination detector.
"""
from __future__ import annotations

import pytest

from detect_boolean_stringification_hallucination import ActionModule, is_boolean_stringification_hallucination

# A confirmed-shipped occurrence that got past verify.yml's own LLM
# re-check before this deterministic check existed.
REAL_MR_42_FINDING = {
    "confidence": "HIGH",
    "description": (
        "Jinja2 string comparison instead of boolean: the `correct` field is set "
        "via a Jinja2 expression `{{ structured == benchmark_tasks[idx].expected }}`, "
        "which yields the Python string `'True'` or `'False'`, not an actual boolean. "
        "Later, `selectattr('correct')` (lines 225, 229) filters on truthiness — but "
        "in Ansible/Jinja2, the string `'False'` is truthy (it's a non-empty string), "
        "so every task will always be counted as correct regardless of actual result. "
        "The same bug exists in the Gemini normalize task at line 189."
    ),
    "evidence": 'correct: "{{ structured == benchmark_tasks[idx].expected }}"',
    "file": "examples/provider_interop_comparison.yml",
    "fix": (
        "Use `correct: \"{{ structured == benchmark_tasks[idx].expected | bool }}\"` — "
        "but that still yields a string. Instead, use the Ansible `bool` filter on the "
        "selectattr side: change `selectattr('correct')` to "
        "`selectattr('correct', 'equalto', 'True')` in both aggregate lines, or "
        "restructure the entry to use a native boolean by wrapping in a `| bool` filter."
    ),
    "lens": "Functionality",
    "line": 157,
    "severity": "Critical",
}

# Other real findings from the same batch (same file, same run) that
# must NOT match, proving this check doesn't over-trigger just because
# it's scoped to the same file/finding-set as the real positive.
REAL_MR_42_SIBLING_FINDINGS = [
    pytest.param(
        {
            "description": (
                "GCP project ID is hardcoded in the playbook. While this is an "
                "examples file and the project ID alone is not a full credential, "
                "it exposes internal infrastructure naming."
            ),
            "evidence": "claude_vertex_project: example-gcp-project",
            "fix": "Replace the hardcoded project ID with a variable reference.",
        },
        id="gcp-project-id-hardcoded",
    ),
    pytest.param(
        {
            "description": (
                "Accumulator reset at line 112 sets `claude_results: []`, but the "
                "normalize task at line 150 still uses `| default([])` fallback."
            ),
            "evidence": 'claude_results: "{{ claude_results | default([]) + [entry] }}"',
            "fix": "Remove `| default([])` from both normalize tasks.",
        },
        id="accumulator-default-footgun",
    ),
    pytest.param(
        {
            "description": "The header comment block (lines 1-49) is 49 lines long.",
            "evidence": "# Compare aknochow.claude and aknochow.gemini...",
            "fix": "Move the design-rationale sections into a companion doc.",
        },
        id="header-comment-too-long",
    ),
]

# Paraphrased variants of the same claim shape -- must match, proving
# the check generalizes beyond one exact wording.
OTHER_WORDING_VARIANTS = [
    pytest.param(
        {
            "evidence": 'silent_failure: "{{ tool_calls[0].input.silent_failure | default(false) }}"',
            "description": (
                "A boolean value extracted from a Claude tool-call input dict via a "
                'quoted Jinja expression gets rendered/stored as the literal string '
                '"False", which Python/Jinja then treats as truthy, silently breaking '
                "every downstream if that_value: check."
            ),
            "fix": "Coerce with | bool at the consumption site.",
        },
        id="dict-key-default-false-jinja-expression",
    ),
    pytest.param(
        {
            "evidence": "verify_result_severity_assessed: false",
            "description": (
                "This bare YAML boolean literal set via set_fact will be stringified "
                'to "False" by Ansible templating, silently breaking every downstream '
                "if that_value: check since non-empty strings are truthy."
            ),
            "fix": "Wrap in | bool when consumed downstream.",
        },
        id="bare-yaml-literal-no-jinja-at-all",
    ),
    pytest.param(
        {
            "evidence": 'requires_execution: "{{ tool_calls[0].input.requires_execution | default(false) }}"',
            "description": (
                "In Ansible 2.17.1, this string, e.g. 'False', is what a bare Jinja "
                "set_fact assignment actually produces, so the downstream truthy check "
                "always evaluates true regardless of the real value."
            ),
            "fix": "Wrap in | bool when consumed downstream.",
        },
        id="embedded-dots-between-keywords-within-bridge-window",
    ),
    pytest.param(
        {
            "evidence": 'some_var: "{{ some_expr }}"',
            "description": (
                "This Jinja set_fact always stores as a string the result, breaking "
                "downstream truthy selectattr checks regardless of the actual result."
            ),
            "fix": "Coerce with | bool at the consumption site.",
        },
        id="third-person-stores-not-just-store-or-stored",
    ),
]

# Negative controls: genuinely different findings that share a word or
# two with the claim shape but must NOT match -- proving this check is
# scoped narrowly, not a general "mentions string/boolean" detector.
NEGATIVE_CONTROLS = [
    pytest.param(
        {
            "evidence": 'response = requests.get(url).json(); if response["active"]:',
            "description": (
                "The upstream REST API returns active as the literal JSON string "
                '"true"/"false" rather than a JSON boolean, so this truthiness check '
                "always passes since a non-empty string is truthy in Python."
            ),
            "fix": "Parse with json.loads or compare against the string explicitly.",
        },
        id="real-rest-api-string-bool-mismatch-not-jinja",
    ),
    pytest.param(
        {
            "evidence": 'some_flag: "{{ compute_something() }}"',
            "description": (
                "This function returns a string when it should return a boolean "
                "based on the docstring contract, an unrelated type-hint mismatch."
            ),
            "fix": "Fix the return type annotation.",
        },
        id="unrelated-type-hint-mismatch",
    ),
    pytest.param(
        {
            "evidence": "if isinstance(x, bool): raise TypeError('unexpected boolean')",
            "description": "A totally unrelated finding about bool handling in a validator.",
            "fix": "Remove the overly strict isinstance check.",
        },
        id="unrelated-bool-mention-no-truthiness-claim",
    ),
]


def test_real_mr_42_finding_is_detected():
    assert is_boolean_stringification_hallucination(REAL_MR_42_FINDING) is True


@pytest.mark.parametrize("finding", REAL_MR_42_SIBLING_FINDINGS)
def test_sibling_findings_in_same_batch_are_not_flagged(finding):
    assert is_boolean_stringification_hallucination(finding) is False


@pytest.mark.parametrize("finding", OTHER_WORDING_VARIANTS)
def test_other_documented_wordings_are_detected(finding):
    assert is_boolean_stringification_hallucination(finding) is True


@pytest.mark.parametrize("finding", NEGATIVE_CONTROLS)
def test_negative_controls_are_not_flagged(finding):
    assert is_boolean_stringification_hallucination(finding) is False


def test_missing_fields_default_to_empty_and_do_not_crash():
    assert is_boolean_stringification_hallucination({}) is False


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "detect_boolean_stringification_hallucination"
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


def test_action_module_returns_true_for_the_real_finding():
    result = _run_action_module({"finding": REAL_MR_42_FINDING})
    assert "failed" not in result
    assert result["is_hallucination"] is True
    assert result["changed"] is False


def test_action_module_fails_loudly_when_finding_arg_missing():
    result = _run_action_module({})
    assert result["failed"] is True
