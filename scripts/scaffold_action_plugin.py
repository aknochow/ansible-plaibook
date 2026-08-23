#!/usr/bin/env python3
"""Scaffold generator for a new action_plugins/*.py + test_*.py pair.

Item build-action-plugin-scaffold-generator from
handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml. NOT a
Jinja-to-Python translator (rejected -- see that handoff's own
context: this codebase's Jinja has accumulated incident-driven
footguns a transpiler would faithfully preserve rather than let anyone
clean up). Generates the MECHANICAL boilerplate every one of the seven
real ports so far has hand-written near-identically -- ActionModule's
arg-extraction/presence-check/result-shape, and the test file's
ActionModule wiring smoke test (the `_FakeShell`/`_FakeConnection`/
`_FakeTask`/`_run_action_module` block, byte-for-byte identical across
every existing plugin test file except the action name and arg dict) --
leaving every actual behavioral decision (the pure function's body, the
equivalence-testing method, the fixture data, custom validation/
exceptions) as a TODO for the plugin's author. Built now, not before
the first three-plus real ports (filter_self_refuted_findings,
compute_review_scores, dedupe_findings, prepare_findings_for_
verification, merge_verify_result, strip_verify_index,
compute_pipeline_cost) existed, so it generalizes from real patterns
rather than speculative ones, per that item's own explicit sequencing.

Usage:
    python3 scripts/scaffold_action_plugin.py <plugin_name> \\
        --args arg1,arg2 [--description "one-line description"]

Refuses to overwrite existing files -- a generator that silently
clobbers hand-written plugin code would be worse than no generator.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_PLUGINS_DIR = REPO_ROOT / "action_plugins"

_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _pascal_case(snake_case_name: str) -> str:
    return "".join(word.capitalize() for word in snake_case_name.split("_"))


def _plugin_module_template(plugin_name: str, args: list[str], description: str) -> str:
    class_name = _pascal_case(plugin_name)
    error_class = f"{class_name}Error"
    arg_params = ", ".join(args)
    arg_extractions = "\n".join(f'        {arg} = self._task.args.get("{arg}")' for arg in args)
    none_checks = " or ".join(f"{arg} is None" for arg in args)
    valid_args_tuple = ", ".join(f'"{arg}"' for arg in args)
    if len(args) == 1:
        valid_args_tuple += ","
    call_kwargs = ", ".join(f"{arg}={arg}" for arg in args)
    # Resolved here, at generation time -- not a runtime ternary in the
    # generated code, since len(args) is already known now.
    if len(args) == 1:
        missing_args_msg = f"{plugin_name} requires a '{args[0]}' argument"
    else:
        missing_args_msg = f"{plugin_name} requires {', '.join(repr(arg) for arg in args)} arguments"

    return f'''# -*- coding: utf-8 -*-
"""Shared action plugin: {description}.

TODO(scaffold): fill in the migration-roadmap context this plugin
belongs to (which handoff item, which Jinja task(s) it replaces), and
describe the equivalence-verification method you used -- a live
Templar render for simple per-item/whole-list expressions (see
compute_review_scores.py, compute_pipeline_cost.py), or a frozen real
baseline captured by running the actual pre-port task file for
anything spanning multiple interdependent tasks with cross-item state
(see dedupe_findings.py's module docstring for why that distinction
matters -- a bare jinja2.Environment/NativeEnvironment is NOT
equivalent to Ansible's real Templar and will misrepresent behavior).

TODO(scaffold): document the original Jinja expression(s) verbatim
here, and any deliberate divergence (disclosed, not silent -- see
dedupe_findings.py's output-order divergence or compute_pipeline_cost.py's
strict-vs-lenient totals-vs-per-call inconsistency for the level of
detail expected).
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase


class {error_class}(ValueError):
    """TODO(scaffold): raised when ... -- delete this class entirely if {plugin_name} needs no validation."""


def {plugin_name}({arg_params}):
    """TODO(scaffold): one-line summary of what this pure function computes."""
    raise NotImplementedError("TODO(scaffold): implement {plugin_name}")


class ActionModule(ActionBase):
    """TODO(scaffold): one-line summary -- real Python instead of <the Jinja tasks this replaces>."""

    _requires_connection = False
    _VALID_ARGS = frozenset(({valid_args_tuple}))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

{arg_extractions}
        if {none_checks}:
            result["failed"] = True
            result["msg"] = "{missing_args_msg}"
            return result

        try:
            computed = {plugin_name}({call_kwargs})
        except {error_class} as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result

        result["changed"] = False
        # TODO(scaffold): result["findings"] = computed, or result.update(computed)
        # if the pure function returns a dict of multiple named outputs
        # (see compute_review_scores.py/compute_pipeline_cost.py for that shape).
        result["result"] = computed
        return result
'''


def _test_module_template(plugin_name: str, args: list[str], description: str) -> str:
    fake_task_args = ", ".join(f'"{arg}": {arg}' for arg in args)
    run_action_params = ", ".join(args)

    return f'''# -*- coding: utf-8 -*-
"""Behavioral tests for {plugin_name}.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

TODO(scaffold): describe your baseline fixture and how it was captured
(a live Templar render, or a frozen real pre-port baseline -- see
{plugin_name}.py's module docstring and this scaffold's own header
comment for which one fits).
"""
from __future__ import annotations

import pytest

from {plugin_name} import ActionModule, {_pascal_case(plugin_name)}Error, {plugin_name}

# TODO(scaffold): replace with a real fixture, ideally captured
# verbatim from a real Templar render or a real pre-port baseline run
# -- not hand-derived, which is exactly the class of transcription
# error this migration's equivalence-testing discipline exists to
# catch (see handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml).
BASELINE_FIXTURE = {{}}
BASELINE_RESULT = {{}}


def test_matches_baseline():
    # TODO(scaffold): assert {plugin_name}(...) == BASELINE_RESULT
    raise NotImplementedError("TODO(scaffold): write the real equivalence test")


# TODO(scaffold): add behavior-specific tests here -- edge cases,
# malformed-input error paths, anything the pure function's docstring
# promises. See the sibling plugins' test files for the level of
# coverage expected (baseline equivalence, plus every documented
# divergence/edge case called out in the module docstring).


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "{plugin_name}"
        self.async_val = False
        self.check_mode = False


def _run_action_module({run_action_params}):
    action = ActionModule(
        task=_FakeTask({{{fake_task_args}}}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={{}})


def test_action_module_requires_all_args():
    action = ActionModule(
        task=_FakeTask({{}}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={{}})
    assert result["failed"] is True


# TODO(scaffold): add test_action_module_run_matches_pure_function once
# {plugin_name}() and BASELINE_FIXTURE/BASELINE_RESULT are real (see any
# sibling plugin's test file for the exact shape).
'''


def generate(plugin_name: str, args: list[str], description: str) -> tuple[Path, Path]:
    if not _VALID_NAME.match(plugin_name):
        raise ValueError(f"plugin_name must be lower_snake_case (got: {plugin_name!r})")

    # Every element gets f-string-interpolated directly into generated
    # Python source (arg extraction lines, the ActionModule's _VALID_ARGS
    # tuple, the test file's fake-task-args dict) -- the same
    # lower_snake_case check plugin_name already gets, so a bad value
    # can't produce broken (or, if someone gets creative, unintended)
    # generated code instead of a clear error here.
    for arg in args:
        if not _VALID_NAME.match(arg):
            raise ValueError(f"arg names must be lower_snake_case (got: {arg!r})")

    # description only ever lands inside a triple-quoted docstring or a
    # single-line comment -- a newline or an embedded '"""' would either
    # break the docstring's own delimiters or silently truncate/corrupt
    # the generated comment.
    if "\n" in description or '"""' in description:
        raise ValueError("description must be a single line and must not contain '\"\"\"'")

    plugin_path = ACTION_PLUGINS_DIR / f"{plugin_name}.py"
    test_path = ACTION_PLUGINS_DIR / f"test_{plugin_name}.py"
    for path in (plugin_path, test_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing file: {path}")

    plugin_path.write_text(_plugin_module_template(plugin_name, args, description))
    test_path.write_text(_test_module_template(plugin_name, args, description))
    return plugin_path, test_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plugin_name", help="lower_snake_case name, e.g. compute_something")
    parser.add_argument(
        "--args", required=True, help="comma-separated required argument names, e.g. findings,severity_points"
    )
    parser.add_argument(
        "--description", default="TODO(scaffold): one-line description", help="one-line description used in docstrings"
    )
    parsed = parser.parse_args(argv)

    args = [arg.strip() for arg in parsed.args.split(",") if arg.strip()]
    if not args:
        parser.error("--args must list at least one argument name")

    try:
        plugin_path, test_path = generate(parsed.plugin_name, args, parsed.description)
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # os.path.relpath, not Path.relative_to -- the latter raises if
    # plugin_path/test_path aren't under REPO_ROOT, which is only true
    # when ACTION_PLUGINS_DIR hasn't been overridden (e.g. in tests).
    print(f"Generated {os.path.relpath(plugin_path, REPO_ROOT)}")
    print(f"Generated {os.path.relpath(test_path, REPO_ROOT)}")
    print("Both files are TODO-scaffolded, not working code -- fill in the pure function, ActionModule result shape, and real tests before use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
