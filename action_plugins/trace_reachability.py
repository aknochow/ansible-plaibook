# -*- coding: utf-8 -*-
"""Shared action plugin: static, AST-based reachability trace for one line of Python source.

Used by verify_turn.yml to check reachability/control-flow claims
mechanically rather than relying on a model to trace guard clauses
correctly by reading carefully.

Deliberately partial, not a substitute for execution-based
verification: this enumerates guard clauses that unconditionally exit
before a target line, using nothing but Python's own `ast` module. It
never evaluates whether any guard's condition is actually true, never
executes anything, and never traces into other functions or callers. A
finding can still be wrong even after this tool reports no guards (a
caller might never reach this function at all) or reports guards whose
conditions never actually hold in practice. Every result states this
limitation explicitly via its `note` field, so a passing static check
is never mistaken for full proof.

`ast` only parses Python; Ansible task reachability (when:, block/
rescue/always) is a structurally different problem this tool cannot
touch. Callers must not point this tool at non-Python files;
`NotPythonSourceError` is the signal when they do.

This plugin takes already-read `source` text, not a file path, and has
no filesystem or subprocess access at all; the caller handles reading
the file and any path-safety checks before calling this plugin.

Bounded, not a full control-flow graph: tracks a sibling `if` statement
whose body and/or orelse unconditionally exits (return/raise/continue/
break, including one level of nested if/else where both branches exit)
at the same nesting depth as the target line, or an enclosing block the
target line is nested inside (function body, if/elif/else, for/while/
with, try/except/else/finally, one level of recursion into whichever
branch actually contains the target line). Does not do full dataflow
analysis, does not resolve `assert` statements as guards (their failure
mode depends on whether assertions are enabled, a runtime concern out
of scope for a static check), and does not trace across function
boundaries. See test_trace_reachability.py for what's covered and what
deliberately isn't.
"""
from __future__ import annotations

import ast

from ansible.plugins.action import ActionBase

_EXIT_NODE_TYPES = (ast.Return, ast.Raise, ast.Continue, ast.Break)
_EXIT_KIND_NAMES = {
    ast.Return: "return",
    ast.Raise: "raise",
    ast.Continue: "continue",
    ast.Break: "break",
}
# Compound statement types whose body/orelse (and, for Try, handlers/
# finalbody) may need recursing into to find which nested block actually
# contains the target line. match/case is deliberately excluded: match
# arms have a different shape (list of MatchCase, not body/orelse) that
# would need its own handling.
_RECURSABLE_TYPES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)


class NotPythonSourceError(SyntaxError):
    """Raised when the given source text isn't valid Python."""


def _exit_kind(stmt: ast.stmt) -> str | None:
    for node_type, name in _EXIT_KIND_NAMES.items():
        if isinstance(stmt, node_type):
            return name
    return None


def _branch_always_exits(stmts: list[ast.stmt]) -> bool:
    """True if a non-empty statement list's last statement is a guaranteed exit.

    Recurses one level into a trailing if/else where BOTH branches
    themselves always exit -- e.g. `if x: return 1 else: return 2` at the
    end of a guard's body still counts as an unconditional exit. Does not
    recurse into try/except here deliberately: whether a try body's exit
    actually fires depends on whether an exception is raised first, which
    this static pass doesn't attempt to resolve.
    """
    if not stmts:
        return False
    last = stmts[-1]
    if _exit_kind(last) is not None:
        return True
    if isinstance(last, ast.If) and last.orelse:
        return _branch_always_exits(last.body) and _branch_always_exits(last.orelse)
    return False


def _condition_source(node: ast.expr, source_lines: list[str]) -> str:
    if hasattr(ast, "unparse"):
        try:
            return ast.unparse(node)
        except Exception:  # pragma: no cover -- unparse is best-effort only
            pass
    # Fallback for anything unparse can't handle: the raw source line(s)
    # the condition spans, trimmed. Still useful as evidence even if not
    # a clean re-serialization of the AST node.
    start, end = node.lineno, getattr(node, "end_lineno", node.lineno)
    return " ".join(line.strip() for line in source_lines[start - 1 : end])


def _collect_guards(
    body: list[ast.stmt], line: int, source_lines: list[str], guards: list[dict]
) -> bool:
    """Walk `body` in order, recording guard clauses that run before `line`.

    Returns True once the target line has been located inside this body
    (directly, or inside a nested block this function recursed into) --
    callers use this to know whether the line was found at all anywhere
    in the enclosing function.
    """
    for stmt in body:
        stmt_start = stmt.lineno
        stmt_end = getattr(stmt, "end_lineno", stmt_start)

        if stmt_end < line:
            # A sibling statement, entirely before the target line,
            # unconditionally. An `if` whose body unconditionally exits is
            # a guard whenever its condition holds, regardless of whether
            # an else exists. The else branch is checked independently: if
            # it also unconditionally exits, that's a guard on the
            # condition's negation. Both can fire on the same statement
            # (an if/else where both branches exit), reported as two
            # guards.
            if isinstance(stmt, ast.If):
                if _branch_always_exits(stmt.body):
                    guards.append(
                        {
                            "line": stmt.lineno,
                            "condition": _condition_source(stmt.test, source_lines),
                            "exits_via": _exit_kind(stmt.body[-1]) or "nested-if-both-branches",
                        }
                    )
                if stmt.orelse and _branch_always_exits(stmt.orelse):
                    guards.append(
                        {
                            "line": stmt.lineno,
                            "condition": "not (" + _condition_source(stmt.test, source_lines) + ")",
                            "exits_via": _exit_kind(stmt.orelse[-1]) or "nested-if-both-branches",
                        }
                    )
            continue

        if stmt_start > line:
            # Statements are in source order within a body -- nothing
            # from here on can contain an earlier line.
            return False

        # This statement's own range contains the target line.
        if not isinstance(stmt, _RECURSABLE_TYPES):
            # A simple (non-compound) statement spanning the target line
            # -- found it, nothing further to recurse into.
            return True

        if isinstance(stmt, ast.If):
            if stmt.body and stmt.body[0].lineno <= line <= (getattr(stmt.body[-1], "end_lineno", stmt.body[-1].lineno)):
                return _collect_guards(stmt.body, line, source_lines, guards)
            if stmt.orelse:
                return _collect_guards(stmt.orelse, line, source_lines, guards)
            return True  # line falls on the `if` header itself
        if isinstance(stmt, ast.Try):
            for block in (stmt.body, stmt.orelse, stmt.finalbody):
                if block and block[0].lineno <= line <= getattr(block[-1], "end_lineno", block[-1].lineno):
                    return _collect_guards(block, line, source_lines, guards)
            for handler in stmt.handlers:
                if handler.body and handler.body[0].lineno <= line <= getattr(
                    handler.body[-1], "end_lineno", handler.body[-1].lineno
                ):
                    return _collect_guards(handler.body, line, source_lines, guards)
            return True  # line falls on a try/except/finally header line
        # For/AsyncFor/While/With/AsyncWith: single `body` to recurse into.
        return _collect_guards(stmt.body, line, source_lines, guards)

    return False


def _innermost_function_containing(tree: ast.Module, line: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    best = None
    best_span = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", None)
        if end is None or not (node.lineno <= line <= end):
            continue
        span = end - node.lineno
        if best is None or span < best_span:
            best, best_span = node, span
    return best


def trace_reachability(source: str, line: int) -> dict:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise NotPythonSourceError(f"not valid Python source: {exc}") from exc

    source_lines = source.splitlines()
    function = _innermost_function_containing(tree, line)
    if function is None:
        return {
            "function_name": None,
            "function_start_line": None,
            "line_found_in_function": False,
            "guards_before_line": [],
            "note": (
                "STATIC ANALYSIS (syntactic only): line {line} is not inside any "
                "function body in this source (module-level code, a class body, "
                "or an out-of-range line number). This tool only traces "
                "reachability within function bodies."
            ).format(line=line),
        }

    guards: list[dict] = []
    found = _collect_guards(function.body, line, source_lines, guards)

    if not found:
        note = (
            "STATIC ANALYSIS (syntactic only): line {line} was not located inside "
            "{name}'s body (it may be a decorator/signature line, or outside the "
            "function's actual statement range). No guard analysis performed."
        ).format(line=line, name=function.name)
    elif guards:
        note = (
            "STATIC ANALYSIS (syntactic only) -- does not execute any code and "
            "does not evaluate whether the guard condition(s) below are actually "
            "true in the scenario your finding describes; does not trace into "
            "other functions/callers. {count} guard clause(s) unconditionally "
            "exit {name} before reaching line {line} if their condition holds. "
            "The target line is reachable only when every listed guard's "
            "condition is false. Confirming whether that's actually possible in "
            "the finding's claimed scenario requires runtime/execution-based "
            "verification, not this tool."
        ).format(count=len(guards), name=function.name, line=line)
    else:
        note = (
            "STATIC ANALYSIS (syntactic only): no guard clauses found between "
            "{name}'s entry and line {line} -- the line executes unconditionally "
            "within this function's own body. This does NOT account for whether "
            "callers of {name} ever actually reach this call in the first place."
        ).format(name=function.name, line=line)

    return {
        "function_name": function.name,
        "function_start_line": function.lineno,
        "line_found_in_function": found,
        "guards_before_line": guards,
        "note": note,
    }


class ActionModule(ActionBase):
    """AST-based static reachability trace -- real Python instead of trusting the model to read carefully."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("source", "line"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        source = self._task.args.get("source")
        line = self._task.args.get("line")
        if source is None or line is None:
            result["failed"] = True
            result["msg"] = "trace_reachability requires 'source' and 'line' arguments"
            return result

        try:
            result["changed"] = False
            result["trace"] = trace_reachability(source, int(line))
        except NotPythonSourceError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
        return result
