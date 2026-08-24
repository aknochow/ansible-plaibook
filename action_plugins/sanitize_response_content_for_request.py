# -*- coding: utf-8 -*-
"""Shared action plugin: strip response-only fields before round-tripping
an assistant turn's content blocks back into the next request's message
history.

explore_turn.yml/verify_turn.yml both append
`{{ *_turn_result.message.content }}` (the raw SDK response, dumped via
`message.model_dump()` in aknochow.claude's own message.py) directly
into the next turn's conversation history. The Anthropic Messages API's
response schema for a `tool_use` content block is not always identical
to its request schema -- a newer SDK version can add response-only
fields (e.g. `toolset_name`) that the request side rejects outright
with `Extra inputs are not permitted`, breaking every multi-turn
tool-calling conversation the moment the installed SDK version drifts
ahead of what this pipeline was built against. No upper-bound pin
exists on `anthropic[vertex]` (aknochow.claude's own requirements.txt),
so this is a real, not hypothetical, version-drift risk.

Allowlist chosen from the Messages API's own documented request schema
per block type, not "whatever this SDK version happens to include" --
future response-only additions are dropped automatically instead of
needing a new field name added here every time the SDK adds one.
Unknown block types are passed through unchanged rather than guessed
at (this pipeline doesn't currently enable extended thinking, whose
`thinking`/`redacted_thinking` blocks have their own round-trip
requirements this allowlist doesn't attempt to model).
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase

_ALLOWED_KEYS_BY_TYPE = {
    "text": frozenset(("type", "text")),
    "tool_use": frozenset(("type", "id", "name", "input")),
}


def sanitize_response_content_for_request(content: list[dict]) -> list[dict]:
    """Drop response-only fields per content-block type -- private to explore_turn.yml/verify_turn.yml."""
    sanitized = []
    for block in content:
        allowed_keys = _ALLOWED_KEYS_BY_TYPE.get(block.get("type"))
        if allowed_keys is None:
            sanitized.append(block)
        else:
            sanitized.append({key: value for key, value in block.items() if key in allowed_keys})
    return sanitized


class ActionModule(ActionBase):
    """Strip response-only content-block fields -- real Python instead of a per-type Jinja filter chain."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("content",))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        content = self._task.args.get("content")
        if content is None:
            result["failed"] = True
            result["msg"] = "sanitize_response_content_for_request requires a 'content' argument"
            return result

        result["changed"] = False
        result["content"] = sanitize_response_content_for_request(content)
        return result
