# -*- coding: utf-8 -*-
"""Jinja filter: adapt this repo's Claude-shaped JSON Schema dicts for
Gemini's response_schema.

roles/review/vars/main.yml's security_findings_schema/review_findings_schema/
continuity_audit_schema are plain JSON Schema, written for Claude's
input_schema (which tolerates/expects additionalProperties: false).
Gemini's response_schema is a restricted OpenAPI-3.0 SUBSET of JSON
Schema and does not support additionalProperties at all -- sending it
is rejected outright by the Developer API with a real 400
INVALID_ARGUMENT ("Unknown name \"additional_properties\""), confirmed
live 2026-08-24 against gemini-3.7-flash, not a guessed incompatibility.

This filter recursively drops every additionalProperties key so the
SAME schema source (no forked Gemini-only copy to drift out of sync)
works for both providers' dispatch files.

It also rewrites a JSON-Schema nullable-union `type: [X, "null"]` (this
repo's verify_verdict_schema uses this for `continues_finding_id`,
which is a real string when the agent claims continuity and legitimately
null otherwise) into Gemini's OpenAPI-subset form `type: X, nullable:
true`. Confirmed live (2026-08-24, gemini-3.5-flash): sending the raw
`['string', 'null']` list is rejected by the SDK's own pydantic
validation before any request is made ("Input should be
'TYPE_UNSPECIFIED', 'STRING', ... [type=enum, input_value=['string',
'null']]") -- Gemini's schema has no concept of a type union, only a
single scalar type plus a separate `nullable` flag.
"""
from __future__ import annotations

import copy


def _strip(node):
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if key == "additionalProperties":
                continue
            if key == "type" and isinstance(value, list):
                non_null_types = [t for t in value if t != "null"]
                if "null" in value and len(non_null_types) == 1:
                    result["type"] = non_null_types[0]
                    result["nullable"] = True
                    continue
                # Multiple non-null types in a union isn't representable
                # in Gemini's schema either; leave as-is so the API's own
                # error surfaces rather than guessing at a collapse.
                result[key] = _strip(value)
                continue
            result[key] = _strip(value)
        return result
    if isinstance(node, list):
        return [_strip(item) for item in node]
    return node


def to_gemini_schema(schema: dict) -> dict:
    """Return a deep copy of `schema` with every additionalProperties key removed."""
    return _strip(copy.deepcopy(schema))


def to_gemini_function_declarations(tools: list) -> list:
    """Translate this pipeline's Claude-shaped tool dicts (name/description/input_schema)
    into Gemini's function_declarations shape (name/description/parameters), stripping
    additionalProperties the same way to_gemini_schema() does for response_schema.

    Used by dispatch_verify_turn_attempt_gemini.yml to reuse the SAME
    explore_tools/trace_reachability_tool/report_verdict tool
    definitions (roles/review/vars/main.yml) the Claude side already
    uses, rather than a second Gemini-only copy that could drift.
    """
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": to_gemini_schema(tool["input_schema"]),
        }
        for tool in tools
    ]


class FilterModule:
    def filters(self):
        return {
            "to_gemini_schema": to_gemini_schema,
            "to_gemini_function_declarations": to_gemini_function_declarations,
        }
