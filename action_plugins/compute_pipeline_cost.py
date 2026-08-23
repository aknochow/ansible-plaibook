# -*- coding: utf-8 -*-
"""Shared action plugin: pipeline-wide token totals and per-call cost estimate.

Eighth port for the action-plugin migration roadmap
(handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml, item
port-pipeline-stats-cost-math) -- a clean pure function of
pipeline_agent_calls (list, accumulated by roles/review/tasks/lenses.yml
on every agent dispatch) and pipeline_pricing_table (dict, per-million-
token USD rates keyed by model), independent of merge.yml/verify.yml/
explore.yml.

Equivalence verified against the real, unmodified Jinja expressions via
Ansible's real Templar (not jinja2_native) -- token totals and the
per-call cost formula are both simple accumulation/per-item expressions
with no groupby/sort/cross-item reordering, so a direct render is a
faithful equivalence check. See test_compute_pipeline_cost.py for the
captured baseline.

Original Jinja (roles/review/tasks/pipeline_stats.yml):
    pipeline_total_input_tokens: "{{ pipeline_agent_calls | map(attribute='usage.input_tokens') | map('int') | sum }}"
    (same shape for output/cache_write/cache_read tokens)

    pipeline_unpriced_models: built by looping
    `pipeline_agent_calls | map(attribute='model') | unique | list`
    (unique RAW model strings, first-occurrence order), keeping any
    whose stripped model_base isn't a pipeline_pricing_table key.

    pipeline_call_costs: one entry per call in
    `((input_tokens | default(0) * input_per_million | default(0)) + ... ) / 1000000`,
    with every usage/rate value defaulted to 0 if missing.

Real, confirmed-not-assumed inconsistency in the original Jinja (probed
against a real Templar, not guessed): the totals task has NO
`| default(...)` anywhere -- `map(attribute='usage.output_tokens')`
raises AnsibleUndefinedVariable immediately if any call's usage dict is
missing that key, since Jinja's dotted-attribute resolution fails
before the `int` filter's own default=0 fallback ever gets a chance to
run. The per-call cost formula, by contrast, explicitly wraps every
usage/rate lookup in `| default(0)` and genuinely tolerates a missing
key. In real use this never actually diverges -- every real
pipeline_agent_calls entry's 'usage' comes verbatim from a live
Claude API response, which always populates all four usage fields --
but a faithful port replicates the ACTUAL behavior, not a more
"sensible" unified one: _usage_value_strict (totals) raises on a
missing key, _usage_value_lenient (per-call cost) defaults to 0,
exactly like the two original Jinja expressions do.

Deliberate divergence, not in the Jinja: MissingModelError if any call
lacks a 'model' key entirely. Every real pipeline_agent_calls entry has
always set it (lenses.yml's own append always includes it) -- same
"fail loudly on a structural invariant violation instead of a
confusing crash or silent no-op" rationale as the InvalidFindingError
guards in the sibling plugins.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

_DATED_SNAPSHOT_SUFFIX = re.compile(r"-\d{8}$")

# Paired explicitly, not two separately-ordered tuples zip()'d together
# -- correctness used to depend on both tuples staying in matching
# order by convention alone, verified only by reading both definitions
# side by side.
_USAGE_RATE_PAIRS = (
    ("input_tokens", "input_per_million"),
    ("output_tokens", "output_per_million"),
    ("cache_creation_input_tokens", "cache_write_per_million"),
    ("cache_read_input_tokens", "cache_read_per_million"),
)
_USAGE_KEYS = tuple(usage_key for usage_key, _ in _USAGE_RATE_PAIRS)


class MissingModelError(ValueError):
    """Raised when a pipeline_agent_calls entry has no 'model' key."""


class MissingUsageFieldError(KeyError):
    """Raised when a pipeline_agent_calls entry's usage dict is missing a required field.

    Domain-specific, not a bare KeyError -- so the ActionModule's run()
    only catches errors this plugin itself means to catch, not any
    unrelated KeyError a future bug in compute_pipeline_cost's own
    result-dict construction might raise.
    """


def _model_base(model: str) -> str:
    """Strip a trailing '-YYYYMMDD' dated snapshot suffix.

    e.g. 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'.
    """
    return _DATED_SNAPSHOT_SUFFIX.sub("", model)


def _usage_value_strict(call: dict, key: str) -> int:
    """Matches the totals task's real behavior: raises if 'usage' or `key` is missing (see module docstring)."""
    try:
        return int(call["usage"][key])
    except KeyError as exc:
        raise MissingUsageFieldError(str(exc)) from exc


def _usage_value_lenient(call: dict, key: str) -> int:
    """Matches the per-call cost formula's real behavior: `| default(0)` tolerates a missing 'usage' or `key`."""
    return int((call.get("usage") or {}).get(key) or 0)


def compute_pipeline_cost(pipeline_agent_calls: list[dict], pipeline_pricing_table: dict) -> dict:
    missing_model = sum(1 for call in pipeline_agent_calls if "model" not in call)
    if missing_model:
        raise MissingModelError(f"{missing_model} pipeline_agent_calls entr(y/ies) have no 'model' key at all")

    totals = {key: sum(_usage_value_strict(call, key) for call in pipeline_agent_calls) for key in _USAGE_KEYS}

    seen_models = list(dict.fromkeys(call["model"] for call in pipeline_agent_calls))
    unpriced_models = [model for model in seen_models if _model_base(model) not in pipeline_pricing_table]

    call_costs = []
    for call in pipeline_agent_calls:
        rates = pipeline_pricing_table.get(_model_base(call["model"]), {})
        cost = sum(
            _usage_value_lenient(call, usage_key) * float(rates.get(rate_key) or 0)
            for usage_key, rate_key in _USAGE_RATE_PAIRS
        )
        call_costs.append(cost / 1_000_000)

    return {
        "total_input_tokens": totals["input_tokens"],
        "total_output_tokens": totals["output_tokens"],
        "total_cache_write_tokens": totals["cache_creation_input_tokens"],
        "total_cache_read_tokens": totals["cache_read_input_tokens"],
        "unpriced_models": unpriced_models,
        "call_costs": call_costs,
    }


class ActionModule(ActionBase):
    """Pipeline-wide token totals + per-call cost estimate -- real Python instead of five loop/accumulator tasks."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("pipeline_agent_calls", "pipeline_pricing_table"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        pipeline_agent_calls = self._task.args.get("pipeline_agent_calls")
        pipeline_pricing_table = self._task.args.get("pipeline_pricing_table")
        if pipeline_agent_calls is None or pipeline_pricing_table is None:
            result["failed"] = True
            result["msg"] = "compute_pipeline_cost requires 'pipeline_agent_calls' and 'pipeline_pricing_table' arguments"
            return result

        try:
            computed = compute_pipeline_cost(pipeline_agent_calls, pipeline_pricing_table)
        except MissingModelError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result
        except MissingUsageFieldError as exc:
            result["failed"] = True
            result["msg"] = f"missing usage field: {exc}"
            return result

        result["changed"] = False
        result.update(computed)
        return result
