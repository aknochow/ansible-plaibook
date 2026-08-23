# -*- coding: utf-8 -*-
r"""Shared action plugin: parse `git diff --numstat` output into a per-file stats dict.

Ninth port for the action-plugin migration roadmap
(handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml, item
port-briefing-diff-fetching) -- part of replacing the vendored
prepare_briefing.py (see handoff.ansible-plaibook-coordinator-setup-rebuild-
design.yaml). The actual `git diff --numstat` subprocess call becomes a
plain Ansible ansible.builtin.command task (Ansible is the orchestrator
here, no reason to wrap a single git invocation in Python) -- this
plugin only parses its stdout, which is the genuinely pure part of the
original get_diff_stat().

Original Python (prepare_briefing.py's get_diff_stat, minus the
subprocess call itself):
    stats = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            adds, dels, path = parts
            if "{" in path and " => " in path:
                path = re.sub(r'([^{]*)\{[^}]+ => ([^}]+)\}(.*)', r'\1\2\3', path)
            stats[path] = {
                "additions": int(adds) if adds != "-" else 0,
                "deletions": int(dels) if dels != "-" else 0,
            }
    return stats

The `{old => new}` substitution handles git's rename-shorthand path
notation in numstat output for renamed files with a partial-path common
prefix/suffix, e.g. `src/{old_name.py => new_name.py}` ->
`src/new_name.py`. `-` in either count column means a binary file (git
doesn't compute line-level stats for those) -- preserved as 0, matching
the original's ternary exactly, not raised as an error: a binary file
in a diff is a completely normal, expected case, not a malformed-input
signal.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

_RENAME_SHORTHAND = re.compile(r"([^{]*)\{[^}]+ => ([^}]+)\}(.*)")


def parse_diff_stat(numstat_output: str) -> dict:
    stats = {}
    for line in numstat_output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        adds, dels, path = parts
        path = _RENAME_SHORTHAND.sub(r"\1\2\3", path)
        stats[path] = {
            "additions": int(adds) if adds != "-" else 0,
            "deletions": int(dels) if dels != "-" else 0,
        }
    return stats


class ActionModule(ActionBase):
    """Parse `git diff --numstat` output -- real Python instead of a hand-rolled Jinja loop."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("numstat_output",))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        numstat_output = self._task.args.get("numstat_output")
        if numstat_output is None:
            result["failed"] = True
            result["msg"] = "parse_diff_stat requires a 'numstat_output' argument"
            return result

        result["changed"] = False
        result["stats"] = parse_diff_stat(numstat_output)
        return result
