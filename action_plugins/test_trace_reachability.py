# -*- coding: utf-8 -*-
"""Behavioral tests for trace_reachability.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

Targets the confirmed false positive that motivated this tool: a Major
finding verify.yml itself verified as true, that a direct script test
later disproved because of a pre-existing early-return guard in the
same function, plus the documented boundary of what this static pass
does and doesn't cover.
"""
from __future__ import annotations

import pytest

from trace_reachability import ActionModule, NotPythonSourceError, trace_reachability

# Shape of the confirmed false positive: a finding claimed
# `yaml.dump(merged)` was reachable, missing the `if not merged: return`
# guard a few lines above it in the same function.
_CMD_LIST_SOURCE = """\
import yaml


def cmd_list(profile_data):
    merged = build_merged(profile_data)
    if not merged:
        print("nothing to show")
        return
    yaml.dump(merged)
"""


def test_confirmed_false_positive_shape_reports_the_guard():
    # yaml.dump(merged) is line 9.
    trace = trace_reachability(_CMD_LIST_SOURCE, 9)
    assert trace["function_name"] == "cmd_list"
    assert trace["line_found_in_function"] is True
    assert len(trace["guards_before_line"]) == 1
    guard = trace["guards_before_line"][0]
    assert guard["line"] == 6  # "if not merged:"
    assert guard["exits_via"] == "return"
    assert "not merged" in guard["condition"]
    assert "STATIC ANALYSIS" in trace["note"]
    assert "does not evaluate whether" in trace["note"]


_NO_GUARD_SOURCE = """\
def foo(x):
    y = x + 1
    print(y)
"""


def test_genuinely_reachable_line_reports_no_guards():
    trace = trace_reachability(_NO_GUARD_SOURCE, 3)
    assert trace["line_found_in_function"] is True
    assert trace["guards_before_line"] == []
    assert "no guard clauses found" in trace["note"].lower()


_MODULE_LEVEL_SOURCE = """\
import os

X = 1
"""


def test_line_outside_any_function_reports_no_function():
    trace = trace_reachability(_MODULE_LEVEL_SOURCE, 3)
    assert trace["function_name"] is None
    assert trace["line_found_in_function"] is False
    assert trace["guards_before_line"] == []


_ELSE_BRANCH_SOURCE = """\
def bar(x):
    if x is None:
        return None
    if x > 0:
        return "positive"
    else:
        print("non-positive")
"""


def test_line_inside_an_else_branch_only_sees_preceding_sibling_guards():
    # print() is line 7, inside the else of the SECOND if -- that if's
    # own `x > 0` branch must NOT be reported as a guard blocking this
    # line (the line is reached precisely because x > 0 was false), but
    # the first if's unconditional early return, which genuinely runs
    # before this statement regardless of x, must be.
    trace = trace_reachability(_ELSE_BRANCH_SOURCE, 7)
    assert trace["line_found_in_function"] is True
    assert [g["line"] for g in trace["guards_before_line"]] == [2]


def test_bare_else_keyword_line_is_not_a_real_statement_and_reports_not_found():
    # Documented limitation, not a silent gap: Python's ast gives no line
    # of its own to a bare `else:`/`elif`/`finally:` keyword -- it's a
    # marker between a body and orelse, not a statement. A finding citing
    # exactly that line (rather than the code on the line below it) will
    # report not-found. Unlikely in practice (findings cite actual code),
    # documented here so the boundary is explicit rather than surprising.
    trace = trace_reachability(_ELSE_BRANCH_SOURCE, 6)  # the bare "else:" line
    assert trace["line_found_in_function"] is False


_IF_ELSE_ONLY_IF_EXITS_SOURCE = """\
def f(x):
    if x is None:
        return None
    else:
        do_stuff()
    line_after()
"""


def test_if_else_where_only_the_if_body_exits_is_still_a_guard():
    # A sibling if/else was previously invisible to guard detection
    # whenever it had an else clause at all, even though the if-body's
    # own unconditional exit still makes line_after() unreachable when
    # x is None.
    trace = trace_reachability(_IF_ELSE_ONLY_IF_EXITS_SOURCE, 6)
    assert trace["line_found_in_function"] is True
    assert len(trace["guards_before_line"]) == 1
    guard = trace["guards_before_line"][0]
    assert guard["line"] == 2
    assert "x is None" in guard["condition"]
    assert guard["exits_via"] == "return"


_IF_ELSE_ONLY_ELSE_EXITS_SOURCE = """\
def g(x):
    if x:
        do_stuff()
    else:
        return None
    line_after()
"""


def test_if_else_where_only_the_else_body_exits_reports_a_negated_guard():
    trace = trace_reachability(_IF_ELSE_ONLY_ELSE_EXITS_SOURCE, 6)
    assert trace["line_found_in_function"] is True
    assert len(trace["guards_before_line"]) == 1
    guard = trace["guards_before_line"][0]
    assert guard["line"] == 2
    assert guard["condition"] == "not (x)"
    assert guard["exits_via"] == "return"


_IF_ELSE_BOTH_EXIT_SIBLING_SOURCE = """\
def h(x):
    if x:
        return 1
    else:
        return 2
    line_after()
"""


def test_sibling_if_else_where_both_branches_exit_reports_two_guards():
    # Genuinely dead code (every path through the if/else returns) --
    # reported as two complementary guards (condition and its negation)
    # rather than a dedicated "unreachable regardless" signal, so the
    # tool doesn't need a third guard shape for this case.
    trace = trace_reachability(_IF_ELSE_BOTH_EXIT_SIBLING_SOURCE, 6)
    assert trace["line_found_in_function"] is True
    conditions = {g["condition"] for g in trace["guards_before_line"]}
    assert conditions == {"x", "not (x)"}


_NESTED_BOTH_EXIT_SOURCE = """\
def baz(x):
    if x is None:
        if True:
            return None
        else:
            raise ValueError()
    print("reached")
"""


def test_guard_whose_branches_both_exit_via_nested_if_else_is_detected():
    trace = trace_reachability(_NESTED_BOTH_EXIT_SOURCE, 7)
    assert trace["line_found_in_function"] is True
    assert len(trace["guards_before_line"]) == 1
    assert trace["guards_before_line"][0]["line"] == 2
    assert trace["guards_before_line"][0]["exits_via"] == "nested-if-both-branches"


_FOR_LOOP_SOURCE = """\
def qux(items):
    if not items:
        return
    for item in items:
        print(item)
"""


def test_recurses_into_a_for_loop_body_to_find_the_target_line():
    trace = trace_reachability(_FOR_LOOP_SOURCE, 5)
    assert trace["line_found_in_function"] is True
    assert [g["line"] for g in trace["guards_before_line"]] == [2]


_TRY_EXCEPT_SOURCE = """\
def load(path):
    if path is None:
        return None
    try:
        return open(path).read()
    except OSError:
        print("could not read")
"""


def test_recurses_into_a_try_except_handler_body():
    trace = trace_reachability(_TRY_EXCEPT_SOURCE, 7)
    assert trace["line_found_in_function"] is True
    assert [g["line"] for g in trace["guards_before_line"]] == [2]


_MULTIPLE_GUARDS_SOURCE = """\
def f(x, y):
    if x is None:
        return
    if y is None:
        return
    print(x, y)
"""


def test_multiple_sequential_guards_are_all_reported_in_order():
    trace = trace_reachability(_MULTIPLE_GUARDS_SOURCE, 6)
    assert [g["line"] for g in trace["guards_before_line"]] == [2, 4]


_ASSERT_SOURCE = """\
def g(x):
    assert x is not None
    print(x)
"""


def test_assert_statements_are_deliberately_not_treated_as_guards():
    # Documented gap, not a silent one -- see module docstring. An
    # assert's failure mode depends on whether assertions are enabled at
    # all, a runtime concern this static pass doesn't resolve.
    trace = trace_reachability(_ASSERT_SOURCE, 3)
    assert trace["guards_before_line"] == []


def test_target_line_on_the_guard_return_itself_reports_no_guards():
    # Pointed directly at the guard's own return statement -- trivially
    # reachable from inside that branch, no preceding guard applies.
    trace = trace_reachability(_CMD_LIST_SOURCE, 7)  # the `return` line
    assert trace["line_found_in_function"] is True
    assert trace["guards_before_line"] == []


def test_non_python_source_raises_not_python_source_error():
    with pytest.raises(NotPythonSourceError):
        trace_reachability("this is not { valid python : : :", 1)


def test_line_number_past_end_of_file_reports_not_found():
    trace = trace_reachability(_NO_GUARD_SOURCE, 999)
    assert trace["function_name"] is None
    assert trace["line_found_in_function"] is False


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "trace_reachability"
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
    result = _run_action_module({"source": _CMD_LIST_SOURCE, "line": 9})
    assert "failed" not in result
    assert result["trace"] == trace_reachability(_CMD_LIST_SOURCE, 9)


def test_action_module_requires_source_and_line():
    result = _run_action_module({"line": 9})
    assert result["failed"] is True
    result = _run_action_module({"source": _CMD_LIST_SOURCE})
    assert result["failed"] is True


def test_action_module_surfaces_syntax_error_as_failed_not_an_exception():
    result = _run_action_module({"source": "not { python", "line": 1})
    assert result["failed"] is True
    assert "not valid Python source" in result["msg"]


def test_action_module_accepts_line_as_a_templated_string():
    # Same real-Ansible gotcha class documented in merge_verify_result.py
    # -- a dot-accessed/templated integer can arrive as a string over
    # non-native Jinja. Cast defensively rather than trust the caller.
    result = _run_action_module({"source": _CMD_LIST_SOURCE, "line": "9"})
    assert "failed" not in result
    assert result["trace"]["line_found_in_function"] is True
