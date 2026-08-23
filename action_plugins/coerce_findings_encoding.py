# -*- coding: utf-8 -*-
"""Shared action plugin: coerce a findings value that may be double-JSON-encoded.

Real, reproducible crash (handoff.ansible-plaibook-merge-findings-string-vs-list-
crash.yaml), found by ansible-plaibook-benchmark testing Claude Sonnet 5 as a lens
model: `aknochow.claude.message`'s tool_choice-forced dispatch returns
`tool_calls[0].input` as parsed by the Anthropic/Vertex SDK from the model's
own function-call arguments -- if the MODEL's own generation serializes its
`findings` array as a JSON-encoded STRING value instead of a native nested
array (a real, confirmed LLM structured-output quirk, not a client-side
parsing bug -- `aknochow.claude.message`'s own flatten_response() JSON-parses
top-level TEXT content, not tool_use input at all; lenses.yml's normalization
step reads tool_calls[0].input directly), `structured.findings`/
`explore_report_calls[0].input.findings` ends up a string, not a list.

Confirmed THREE distinct failure shapes from the real captured logs
(/tmp/sonnet-verify-verbose.log, /tmp/sonnet-verify-vvv.log), not just the
one in the handoff's own summary:
1. One side string, one side list: Jinja's `{{ a + b }}` raises "can only
   concatenate str (not 'list') to str" (or the mirror) immediately at
   merge.yml's combine step -- the confirmed repro the handoff describes.
2. BOTH sides string: Python string concatenation SUCCEEDS silently (unlike
   case 1), producing one garbage string that is the two JSON strings
   mashed together with no separator -- not valid JSON, but a normal Python
   str, so nothing fails yet. The crash happens ONE STEP LATER and looks
   completely unrelated: filter_self_refuted_findings.py iterates the
   (string) "findings" list expecting dicts, gets individual CHARACTERS
   instead, and calling `.get()` on a character raises "'AnsibleUnsafeText'
   object has no attribute 'get'" -- confirmed directly from
   sonnet-verify-verbose.log's own combined_findings dump, which rendered
   as a literal one-string YAML block scalar, not a findings list.
3. Same root cause, one level deeper: explore_turn.yml:83's
   `explore_report_calls[0].input.findings` reads from the identical kind
   of raw tool-call input as merge.yml's lens dispatch -- confirmed via
   grep (only two call sites read `.input.findings`/`.structured.findings`
   directly in this role) that this is the same vulnerability shape, not a
   new one, so this plugin is wired into both sites rather than only the
   one the triggering handoff named.

Confirmed this is NOT provably Sonnet-5-specific before building this fix
(explicit instruction, not assumed): 5 live default-tier (claude-opus-4-6)
runs against the exact same corpus case (ansible-plaibook-benchmark's
001-sql-injection-basic, which IS the real fixture that produced the
Sonnet-5 repro -- confirmed identical bug.patch) did not reproduce it, and
no prior review artifact in this project's extensive history
(~/.cache/ansible-plaibook, ~/reviews) shows this pattern with the default tier
either. Absence of evidence in a modest sample isn't proof of impossibility
for a probabilistic model-generation quirk -- the fix is deliberately
model-agnostic (checks the actual runtime type, never the model name) so it
protects against any model exhibiting this, present or future, not just the
one observed to trigger it so far.

Fails loudly (does not silently default to an empty list) when a string
value isn't valid JSON, or JSON-decodes to something other than a list of
dicts -- this codebase's established "don't trust model compliance, fail
loud with a clear message" posture (dedupe_findings.py's InvalidFindingError,
compute_suggested_severity.py's InvalidSeverityInputsError, etc.). Silently
substituting an empty list would DISCARD real findings (potentially a real
Critical/Major security finding) with no signal at all -- a worse failure
mode than a loud, clear error for a tool whose entire purpose is catching
problems.
"""
from __future__ import annotations

import json

from ansible.plugins.action import ActionBase


class InvalidFindingsEncodingError(ValueError):
    """Raised when a findings value is neither a list nor a JSON-encoded string of one.

    Distinct from dedupe_findings.py's InvalidFindingError (which checks
    individual finding dicts for required keys/known severities) -- this
    checks the ENCODING of the findings value itself, one layer up, before
    any individual finding is ever inspected.
    """


def coerce_findings_encoding(value: list | str, label: str) -> list[dict]:
    """Coerce a findings value into a real list of dicts, tolerating a JSON-encoded string.

    `value` is expected to already have passed through `| default([])` at
    the call site for the "key entirely absent" case -- this only handles
    "present, but the wrong type" (a JSON-encoded string, or anything else).
    An already-correct empty or non-empty list passes through unchanged.
    """
    if isinstance(value, list):
        result = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError) as exc:
            raise InvalidFindingsEncodingError(
                f"{label}'s findings field is a string but not valid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise InvalidFindingsEncodingError(
                f"{label}'s findings field, after JSON-decoding the string, is a "
                f"{type(parsed).__name__}, not a list"
            )
        result = parsed
    else:
        raise InvalidFindingsEncodingError(
            f"{label}'s findings field is a {type(value).__name__}, expected a list "
            "or a JSON-encoded string of one"
        )

    non_dict_indices = [i for i, item in enumerate(result) if not isinstance(item, dict)]
    if non_dict_indices:
        raise InvalidFindingsEncodingError(
            f"{label}'s findings field contains non-dict element(s) at index/indices "
            f"{non_dict_indices} after coercion -- the model's structured output is "
            "malformed beyond a simple string-encoding quirk"
        )
    return result


class ActionModule(ActionBase):
    """Coerce a findings value into a real list, tolerating a JSON-encoded-string quirk."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("value", "label"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        if self._task.args.get("value") is None or self._task.args.get("label") is None:
            result["failed"] = True
            result["msg"] = "coerce_findings_encoding requires both 'value' and 'label' arguments"
            return result

        try:
            findings = coerce_findings_encoding(self._task.args["value"], self._task.args["label"])
        except InvalidFindingsEncodingError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result

        result["changed"] = False
        result["findings"] = findings
        return result
