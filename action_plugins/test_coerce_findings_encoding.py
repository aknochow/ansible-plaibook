# -*- coding: utf-8 -*-
"""Behavioral tests for coerce_findings_encoding.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

Covers three encoding shapes a lens response can return: one side
string / one side list (raises immediately in merge.yml if uncoerced),
both sides string (would otherwise silently concatenate into invalid
JSON that crashes later in filter_self_refuted_findings.py), and the
already-correct native-list case (must remain a no-op).
"""
from __future__ import annotations

import pytest

from coerce_findings_encoding import ActionModule, InvalidFindingsEncodingError, coerce_findings_encoding

# A findings array double-encoded as a single JSON string value, the
# shape a lens response occasionally returns.
REAL_SONNET_STRING_ENCODED_FINDINGS = (
    '[{"lens":"Security","file":"app/users.py","line":7,"severity":"Critical",'
    '"description":"SQL injection vulnerability.","evidence":"query = \\"SELECT * '
    "FROM users WHERE email = '%s'\\\" % email\","
    '"confidence":"HIGH","fix":"Use a parameterized query."}]'
)

# A proper native list from a different lens on the same run, showing
# the encoding issue is a per-call quirk, not systemic.
REAL_SONNET_NATIVE_LIST_FINDINGS = [
    {
        "lens": "Functionality",
        "file": "app/users.py",
        "line": 6,
        "severity": "Critical",
        "description": "SQL injection / correctness regression.",
        "evidence": "query = \"SELECT * FROM users WHERE email = '%s'\" % email",
        "confidence": "HIGH",
        "fix": "Use a parameterized query.",
    }
]


def test_native_list_passes_through_unchanged():
    result = coerce_findings_encoding(REAL_SONNET_NATIVE_LIST_FINDINGS, "Review lens")
    assert result == REAL_SONNET_NATIVE_LIST_FINDINGS


def test_empty_list_passes_through_unchanged():
    assert coerce_findings_encoding([], "Security lens") == []


def test_real_sonnet_string_encoded_findings_are_decoded():
    result = coerce_findings_encoding(REAL_SONNET_STRING_ENCODED_FINDINGS, "Security lens")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["file"] == "app/users.py"
    assert result[0]["severity"] == "Critical"


def test_failure_shape_1_one_side_string_one_side_list_both_coerce_then_combine_safely():
    # security=string, review=list. Both sides coerced independently,
    # THEN combined, so the '+' after coercion is always list+list,
    # never a raw str+list that would raise in merge.yml.
    security = coerce_findings_encoding(REAL_SONNET_STRING_ENCODED_FINDINGS, "Security lens")
    review = coerce_findings_encoding(REAL_SONNET_NATIVE_LIST_FINDINGS, "Review lens")
    combined = security + review
    assert len(combined) == 2
    assert {f["lens"] for f in combined} == {"Security", "Functionality"}


def test_failure_shape_2_both_sides_string_coerce_independently_before_any_concatenation():
    # The more dangerous shape: BOTH sides string. Raw str + str would
    # silently succeed as string concatenation, producing invalid-JSON
    # garbage that crashes later in filter_self_refuted_findings.py with
    # a confusing, unrelated error. Coercing each side independently
    # before combining prevents that state from existing.
    security_str = REAL_SONNET_STRING_ENCODED_FINDINGS
    review_str = (
        '[{"lens":"Functionality","file":"app/users.py","line":7,"severity":"Critical",'
        '"description":"Also a regression.","evidence":"query = \\"SELECT * FROM users '
        "WHERE email = '%s'\\\" % email\","
        '"confidence":"HIGH","fix":"Use a parameterized query."}]'
    )
    security = coerce_findings_encoding(security_str, "Security lens")
    review = coerce_findings_encoding(review_str, "Review lens")
    combined = security + review
    assert len(combined) == 2
    assert all(isinstance(f, dict) for f in combined)
    assert combined[0]["file"] == "app/users.py"
    assert combined[1]["file"] == "app/users.py"


def test_malformed_json_string_raises_not_silently_empty():
    with pytest.raises(InvalidFindingsEncodingError, match="not valid JSON"):
        coerce_findings_encoding("[{this is not valid json", "Security lens")


def test_string_that_decodes_to_a_dict_not_a_list_raises():
    with pytest.raises(InvalidFindingsEncodingError, match="not a list"):
        coerce_findings_encoding('{"file": "a.py"}', "Security lens")


def test_string_that_decodes_to_a_list_of_non_dicts_raises():
    with pytest.raises(InvalidFindingsEncodingError, match="non-dict element"):
        coerce_findings_encoding('["just a string", "another string"]', "Security lens")


def test_native_list_containing_a_non_dict_element_raises():
    with pytest.raises(InvalidFindingsEncodingError, match="non-dict element"):
        coerce_findings_encoding([{"file": "a.py"}, "not a dict"], "Review lens")


def test_unexpected_type_raises_with_clear_message():
    with pytest.raises(InvalidFindingsEncodingError, match="expected a list"):
        coerce_findings_encoding(42, "Security lens")


def test_real_sonnet5_extra_data_error_shape_is_caught():
    # Valid JSON followed by trailing garbage produces a different
    # json.JSONDecodeError subtype ("Extra data") than this file's other
    # malformed-string test ("Expecting value"). Both are caught by the
    # same broad except (json.JSONDecodeError, ValueError).
    real_shape_findings = (
        '[{"lens":"Security","file":"app/users.py","line":7,"severity":"Critical",'
        '"description":"x","evidence":"y","confidence":"HIGH","fix":"z"}] TRAILING GARBAGE'
    )
    with pytest.raises(InvalidFindingsEncodingError, match="Extra data"):
        coerce_findings_encoding(real_shape_findings, "Security lens")


def test_error_message_includes_the_label():
    with pytest.raises(InvalidFindingsEncodingError, match="Security lens"):
        coerce_findings_encoding("not json at all {{{", "Security lens")
    with pytest.raises(InvalidFindingsEncodingError, match="Explore agent"):
        coerce_findings_encoding("not json at all {{{", "Explore agent")


# --- ActionModule wiring smoke test (same hand-rolled test-double pattern
# as this codebase's other action plugin test files) --------------------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "coerce_findings_encoding"
        self.async_val = False
        self.check_mode = False


def _run_action_module(value, label):
    action = ActionModule(
        task=_FakeTask({"value": value, "label": label}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(REAL_SONNET_STRING_ENCODED_FINDINGS, "Security lens")
    assert "failed" not in result
    assert result["findings"] == coerce_findings_encoding(REAL_SONNET_STRING_ENCODED_FINDINGS, "Security lens")


def test_action_module_surfaces_a_clear_failure_not_a_raw_traceback():
    result = _run_action_module("not valid json {{{", "Security lens")
    assert result["failed"] is True
    assert "Security lens" in result["msg"]


def test_action_module_requires_both_args():
    action = ActionModule(
        task=_FakeTask({"value": []}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
