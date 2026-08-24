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
"""
from __future__ import annotations

import copy


def _strip(node):
    if isinstance(node, dict):
        return {
            key: _strip(value)
            for key, value in node.items()
            if key != "additionalProperties"
        }
    if isinstance(node, list):
        return [_strip(item) for item in node]
    return node


def to_gemini_schema(schema: dict) -> dict:
    """Return a deep copy of `schema` with every additionalProperties key removed."""
    return _strip(copy.deepcopy(schema))


class FilterModule:
    def filters(self):
        return {"to_gemini_schema": to_gemini_schema}
