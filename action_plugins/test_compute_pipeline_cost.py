# -*- coding: utf-8 -*-
"""Behavioral tests for compute_pipeline_cost.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

Covers a real asymmetry: the totals expressions raise on a missing
usage subkey, while the per-call cost formula's explicit `| default(0)`
tolerates it. See compute_pipeline_cost.py's own module docstring.
"""
from __future__ import annotations

import pytest

from compute_pipeline_cost import (
    ActionModule,
    MissingModelError,
    MissingUsageFieldError,
    _usage_value_lenient,
    compute_pipeline_cost,
)

_EMPTY_USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

PRICING_TABLE = {
    "claude-haiku-4-5": {
        "input_per_million": 1.00, "output_per_million": 5.00,
        "cache_write_per_million": 1.25, "cache_read_per_million": 0.10,
    },
    "claude-opus-4-6": {
        "input_per_million": 5.00, "output_per_million": 25.00,
        "cache_write_per_million": 6.25, "cache_read_per_million": 0.50,
    },
}

WELL_FORMED_CALLS = [
    {
        "target": "org/repo#1", "lens": "security", "model": "claude-haiku-4-5-20251001",
        "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 500},
    },
    {
        "target": "org/repo#1", "lens": "review", "model": "claude-haiku-4-5-20251001",
        "usage": {"input_tokens": 2000, "output_tokens": 300, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1000},
    },
    # A model with no pricing table entry -- must be flagged as
    # unpriced, and its cost excluded (rates default to {}, so 0).
    {
        "target": "org/repo#2", "lens": "explore", "model": "claude-opus-unknown-20260101",
        "usage": {"input_tokens": 500, "output_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    },
]

BASELINE_TOTALS = {
    "total_input_tokens": 3500,
    "total_output_tokens": 600,
    "total_cache_write_tokens": 50,
    "total_cache_read_tokens": 1500,
}
BASELINE_UNPRICED_MODELS = ["claude-opus-unknown-20260101"]
BASELINE_CALL_COSTS = [0.0021125, 0.0036, 0.0]


def test_matches_real_jinja_baseline():
    result = compute_pipeline_cost(WELL_FORMED_CALLS, PRICING_TABLE)
    for key, value in BASELINE_TOTALS.items():
        assert result[key] == value
    assert result["unpriced_models"] == BASELINE_UNPRICED_MODELS
    assert result["call_costs"] == pytest.approx(BASELINE_CALL_COSTS)


def test_unpriced_models_preserves_raw_dated_string_not_stripped_base():
    result = compute_pipeline_cost(WELL_FORMED_CALLS, PRICING_TABLE)
    assert result["unpriced_models"] == ["claude-opus-unknown-20260101"]


def test_unpriced_models_are_unique_first_occurrence_order():
    calls = [
        {"model": "unknown-a-20260101", "usage": _EMPTY_USAGE},
        {"model": "unknown-b-20260101", "usage": _EMPTY_USAGE},
        {"model": "unknown-a-20260101", "usage": _EMPTY_USAGE},  # repeat, must not duplicate
    ]
    result = compute_pipeline_cost(calls, PRICING_TABLE)
    assert result["unpriced_models"] == ["unknown-a-20260101", "unknown-b-20260101"]


def test_dated_snapshot_suffix_is_stripped_for_pricing_lookup():
    calls = [{"model": "claude-haiku-4-5-20251001", "usage": {**_EMPTY_USAGE, "input_tokens": 1000000}}]
    result = compute_pipeline_cost(calls, PRICING_TABLE)
    assert result["unpriced_models"] == []  # stripped base IS in the pricing table
    assert result["call_costs"] == pytest.approx([1.00])  # 1M input tokens @ $1.00/M


def test_bare_model_alias_with_no_dated_suffix_also_matches():
    calls = [{"model": "claude-haiku-4-5", "usage": {**_EMPTY_USAGE, "input_tokens": 1000000}}]
    result = compute_pipeline_cost(calls, PRICING_TABLE)
    assert result["unpriced_models"] == []
    assert result["call_costs"] == pytest.approx([1.00])


def test_unpriced_model_contributes_zero_cost_not_excluded_from_list():
    result = compute_pipeline_cost(WELL_FORMED_CALLS, PRICING_TABLE)
    assert len(result["call_costs"]) == len(WELL_FORMED_CALLS)
    assert result["call_costs"][2] == 0.0


def test_lenient_usage_helper_tolerates_a_missing_usage_subkey():
    # Tested against the private helper directly: in the combined
    # pipeline this case is unreachable, since the strict totals step
    # always runs first and would already have raised on the same
    # sparse call. This documents the per-call formula's own tolerance
    # regardless.
    assert _usage_value_lenient({"usage": {"input_tokens": 10}}, "output_tokens") == 0
    assert _usage_value_lenient({"usage": {"input_tokens": 10}}, "input_tokens") == 10


def test_lenient_usage_helper_tolerates_missing_usage_dict_entirely():
    assert _usage_value_lenient({}, "input_tokens") == 0


def test_totals_raise_on_a_missing_usage_subkey_matching_real_jinja():
    # The totals task has no `| default(...)` anywhere, so a missing
    # usage subkey raises. MissingUsageFieldError is a KeyError subclass,
    # not something to paper over with a more lenient implementation.
    calls = [{"model": "claude-haiku-4-5-20251001", "usage": {"input_tokens": 10}}]  # missing 3 of 4 keys
    with pytest.raises(MissingUsageFieldError):
        compute_pipeline_cost(calls, PRICING_TABLE)


def test_totals_raise_on_missing_usage_dict_entirely():
    calls = [{"model": "claude-haiku-4-5-20251001"}]
    with pytest.raises(MissingUsageFieldError):
        compute_pipeline_cost(calls, PRICING_TABLE)


def test_missing_model_key_raises_instead_of_keyerror():
    calls = [{"usage": {"input_tokens": 10, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}]
    with pytest.raises(MissingModelError, match="no 'model' key"):
        compute_pipeline_cost(calls, PRICING_TABLE)


def test_empty_calls_list_returns_zeroed_totals():
    result = compute_pipeline_cost([], PRICING_TABLE)
    assert result["total_input_tokens"] == 0
    assert result["total_output_tokens"] == 0
    assert result["total_cache_write_tokens"] == 0
    assert result["total_cache_read_tokens"] == 0
    assert result["unpriced_models"] == []
    assert result["call_costs"] == []


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "compute_pipeline_cost"
        self.async_val = False
        self.check_mode = False


def _run_action_module(pipeline_agent_calls, pipeline_pricing_table):
    action = ActionModule(
        task=_FakeTask({"pipeline_agent_calls": pipeline_agent_calls, "pipeline_pricing_table": pipeline_pricing_table}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(WELL_FORMED_CALLS, PRICING_TABLE)
    assert "failed" not in result
    expected = compute_pipeline_cost(WELL_FORMED_CALLS, PRICING_TABLE)
    for key in expected:
        if key == "call_costs":
            assert result[key] == pytest.approx(expected[key])
        else:
            assert result[key] == expected[key]


def test_action_module_requires_both_args():
    action = ActionModule(
        task=_FakeTask({"pipeline_agent_calls": []}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True


def test_action_module_surfaces_missing_usage_subkey_as_a_clear_failure():
    calls = [{"model": "claude-haiku-4-5-20251001", "usage": {"input_tokens": 10}}]
    result = _run_action_module(calls, PRICING_TABLE)
    assert result["failed"] is True
    assert "missing usage field" in result["msg"]
