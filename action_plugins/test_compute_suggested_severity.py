# -*- coding: utf-8 -*-
"""Behavioral tests for compute_suggested_severity.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

Not a port of an existing Jinja implementation -- this lookup table
never existed as Jinja anywhere (axis 1 of handoff.ansible-plaibook-verify-
yml-scope-expansion.yaml is new work). Tests instead assert directly
against the DECIDED design's own stated rules: worst_outcome_category
sets the base, reachability/trust_boundary can only discount (never
raise) severity, silent_failure bumps up by exactly one level applied
last, and the result is always clamped to the closed [Nit, Critical]
range.
"""
from __future__ import annotations

import pytest

from compute_suggested_severity import ActionModule, InvalidSeverityInputsError, compute_suggested_severity


def _severity(
    worst_outcome_category="unauthorized-write-or-rce",
    reachability="realistic",
    trust_boundary="crosses-trust-boundary",
    silent_failure=False,
):
    return compute_suggested_severity(
        reachability=reachability,
        trust_boundary=trust_boundary,
        worst_outcome_category=worst_outcome_category,
        silent_failure=silent_failure,
    )


# --- Base severity from worst_outcome_category alone (all other axes at
# their most-severe/no-discount setting) -----------------------------


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("unauthorized-write-or-rce", "Critical"),
        ("data-corruption", "Critical"),
        ("unauthorized-read", "Major"),
        ("crash-dos", "Major"),
        ("availability-degradation", "Minor"),
        ("cosmetic-style", "Nit"),
    ],
)
def test_base_severity_by_outcome_category(outcome, expected):
    assert _severity(worst_outcome_category=outcome) == expected


# --- reachability only ever discounts, never raises ------------------


def test_theoretical_reachability_discounts_two_levels():
    # unauthorized-read: Major(2) - 2 = Nit(0)
    assert _severity(worst_outcome_category="unauthorized-read", reachability="theoretical-only") == "Nit"


def test_unusual_config_reachability_discounts_one_level():
    assert (
        _severity(worst_outcome_category="unauthorized-write-or-rce", reachability="requires-unusual-config")
        == "Major"
    )


def test_realistic_reachability_is_a_no_op():
    assert _severity(worst_outcome_category="crash-dos", reachability="realistic") == "Major"


def test_reachability_discount_clamps_at_nit_not_negative():
    assert (
        _severity(worst_outcome_category="availability-degradation", reachability="theoretical-only") == "Nit"
    )


# --- trust_boundary only ever discounts when staying within trusted
# input, never for 'unclear' (no free discount for genuine uncertainty)


def test_stays_within_trusted_input_discounts_one_level():
    assert (
        _severity(worst_outcome_category="unauthorized-write-or-rce", trust_boundary="stays-within-trusted-input")
        == "Major"
    )


def test_crosses_trust_boundary_is_a_no_op():
    assert _severity(worst_outcome_category="crash-dos", trust_boundary="crosses-trust-boundary") == "Major"


def test_unclear_trust_boundary_gets_no_discount():
    # Confirms 'unclear' isn't silently treated as either extreme --
    # same base severity as crosses-trust-boundary, not a free downgrade.
    assert _severity(worst_outcome_category="crash-dos", trust_boundary="unclear") == "Major"


# --- silent_failure bumps exactly one level, applied AFTER the two
# discounts, and still clamps at the Critical ceiling ------------------


def test_silent_failure_bumps_one_level():
    assert (
        _severity(worst_outcome_category="availability-degradation", silent_failure=True) == "Major"
    )


def test_silent_failure_bump_clamps_at_critical_not_beyond():
    assert _severity(worst_outcome_category="unauthorized-write-or-rce", silent_failure=True) == "Critical"


# --- defensive string coercion, in case a caller's Ansible templating
# ever does stringify this boolean (verified empirically that it
# currently doesn't -- see the function's own comment) ------------------


def test_stringified_false_does_not_bump_severity():
    assert _severity(worst_outcome_category="availability-degradation", silent_failure="False") == "Minor"


def test_stringified_lowercase_false_does_not_bump_severity():
    assert _severity(worst_outcome_category="availability-degradation", silent_failure="false") == "Minor"


def test_stringified_true_does_bump_severity():
    assert _severity(worst_outcome_category="availability-degradation", silent_failure="True") == "Major"


def test_silent_failure_applies_after_discounts_not_before():
    # unauthorized-write-or-rce: Critical(3) - 2 (theoretical) - 1 (trusted) = Nit(0), then +1 (silent) = Minor(1).
    # Applying the bump BEFORE the discounts (bumping Critical then discounting) would floor at Nit(0) instead.
    result = _severity(
        worst_outcome_category="unauthorized-write-or-rce",
        reachability="theoretical-only",
        trust_boundary="stays-within-trusted-input",
        silent_failure=True,
    )
    assert result == "Minor"


# --- combined scenarios modeling real motivating cases ----------------


def test_theoretical_low_stakes_finding_lands_at_nit():
    result = _severity(
        worst_outcome_category="cosmetic-style",
        reachability="theoretical-only",
        trust_boundary="stays-within-trusted-input",
        silent_failure=False,
    )
    assert result == "Nit"


def test_realistic_untrusted_rce_stays_critical_regardless_of_silent_failure():
    assert (
        _severity(
            worst_outcome_category="unauthorized-write-or-rce",
            reachability="realistic",
            trust_boundary="crosses-trust-boundary",
            silent_failure=False,
        )
        == "Critical"
    )


# --- invalid inputs raise loudly, not a silent default/KeyError -------


def test_unknown_worst_outcome_category_raises():
    with pytest.raises(InvalidSeverityInputsError, match="worst_outcome_category"):
        compute_suggested_severity(
            reachability="realistic",
            trust_boundary="crosses-trust-boundary",
            worst_outcome_category="not-a-real-category",
            silent_failure=False,
        )


def test_unknown_reachability_raises():
    with pytest.raises(InvalidSeverityInputsError, match="reachability"):
        compute_suggested_severity(
            reachability="sometimes",
            trust_boundary="crosses-trust-boundary",
            worst_outcome_category="crash-dos",
            silent_failure=False,
        )


def test_unknown_trust_boundary_raises():
    with pytest.raises(InvalidSeverityInputsError, match="trust_boundary"):
        compute_suggested_severity(
            reachability="realistic",
            trust_boundary="sometimes",
            worst_outcome_category="crash-dos",
            silent_failure=False,
        )


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
        self.action = "compute_suggested_severity"
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
    args = {
        "reachability": "realistic",
        "trust_boundary": "crosses-trust-boundary",
        "worst_outcome_category": "crash-dos",
        "silent_failure": False,
    }
    result = _run_action_module(args)
    assert "failed" not in result
    assert result["suggested_severity"] == compute_suggested_severity(**args)


def test_action_module_treats_silent_failure_false_as_present_not_missing():
    # silent_failure: False is a legitimate value, not a missing arg --
    # same "is None, not falsy" precedent as requires_execution elsewhere
    # in this codebase (merge_verify_result.py).
    args = {
        "reachability": "realistic",
        "trust_boundary": "crosses-trust-boundary",
        "worst_outcome_category": "crash-dos",
        "silent_failure": False,
    }
    result = _run_action_module(args)
    assert "failed" not in result


def test_action_module_requires_all_four_args():
    result = _run_action_module({"reachability": "realistic"})
    assert result["failed"] is True


def test_action_module_fails_clearly_on_unknown_value():
    result = _run_action_module(
        {
            "reachability": "realistic",
            "trust_boundary": "crosses-trust-boundary",
            "worst_outcome_category": "not-a-real-category",
            "silent_failure": False,
        }
    )
    assert result["failed"] is True
    assert "not-a-real-category" in result["msg"]
