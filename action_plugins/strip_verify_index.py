# -*- coding: utf-8 -*-
"""Shared action plugin: strip internal verify-pass-only bookkeeping fields.

Sixth port for the action-plugin migration roadmap
(handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml, item
port-verify-finding-bookkeeping-cluster), piece (4) of that item: a
good small showcase on its own, per the item's own description -- in
Python, a one-line dict comprehension; in Jinja, a three-filter
round-trip (dict2items -> rejectattr -> items2dict) because there's no
direct "delete a key" filter.

Originally stripped only _verify_index (see the equivalence baseline
below). Axis 1 (expand-verify-yml-severity-check,
handoff.ansible-plaibook-verify-yml-scope-expansion.yaml) added a second
verify-pass-only field, _verify_eligible (prepare_findings_for_
verification.py's scope tag) -- same category of internal matching key,
meaningless to persist.yml's summary.json consumers, stripped here too
rather than adding a second whole-list pass for one more key.

Equivalence verified against the real, unmodified Jinja expression via
Ansible's real Templar (not jinja2_native) -- a single whole-list
expression, no groupby/sort/accumulator-across-items behavior, so a
direct render is a faithful equivalence check. See
test_strip_verify_index.py for the captured baseline (_verify_index
only -- _verify_eligible stripping is new work, no prior Jinja
equivalent).

Original Jinja (verify.yml):
    {{ findings | map('dict2items') | map('rejectattr', 'key', 'equalto', '_verify_index')
       | map('items2dict') | list }}
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase

_INTERNAL_KEYS = frozenset(("_verify_index", "_verify_eligible"))


def strip_verify_index(findings: list[dict]) -> list[dict]:
    """Drop internal verify-pass-only keys from every finding -- private to verify.yml/verify_finding.yml."""
    return [{key: value for key, value in finding.items() if key not in _INTERNAL_KEYS} for finding in findings]


class ActionModule(ActionBase):
    """Strip internal verify-pass bookkeeping fields -- real Python instead of a dict2items/rejectattr/items2dict round-trip."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("findings",))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        findings = self._task.args.get("findings")
        if findings is None:
            result["failed"] = True
            result["msg"] = "strip_verify_index requires a 'findings' argument"
            return result

        result["changed"] = False
        result["findings"] = strip_verify_index(findings)
        return result
