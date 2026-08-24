# -*- coding: utf-8 -*-
"""Jinja filters: translate this pipeline's Claude-shaped multi-turn
tool-calling conversation (verify_messages, Claude Messages API
content-block shape) to and from Gemini's contents/parts shape, for
dispatch_verify_turn_attempt_gemini.yml.

Why this exists as real Python rather than a Jinja filter chain: same
rationale as action_plugins/sanitize_response_content_for_request.py --
per-block-type translation with an ordered mix of text/tool_use/
tool_result blocks is exactly the kind of thing that gets unreadable
fast in pure Jinja, and this pipeline already has that precedent.

Two real, live-verified translation requirements this encodes (see
handoff notes / dispatch_verify_turn_attempt_gemini.yml's own header
comment for the live-call evidence):

1. Gemini's function_call parts carry a `thought_signature` that MUST
   be threaded back into the next request's history unchanged, or the
   API rejects the follow-up call outright with 400 INVALID_ARGUMENT
   ("Function call is missing a thought_signature in functionCall
   parts."). Reconstructing a function_call part from only
   id/name/args (which is all aknochow.gemini.generate's own
   `tool_calls` return value carries) silently drops this and breaks
   every multi-turn Gemini tool-calling conversation. This module
   carries the signature through as an internal-only
   `_gemini_thought_signature` key on the normalized tool_use block
   (added to sanitize_response_content_for_request.py's own allowlist
   so it survives that round-trip step too) precisely so it's
   available again here on the next turn.

2. Gemini's function_response part requires a `name` matching the
   original function_call's name, which this pipeline's shared,
   provider-agnostic dispatch_explore_tool.yml (written against
   Claude's tool_result shape, which has no name field) never
   populates. to_gemini_contents() below recovers it by scanning
   forward through the message list and remembering the most recent
   tool_use id->name mapping -- conversation-local state, not a
   change to the shared tool-dispatch code both providers reuse.
"""
from __future__ import annotations


def to_gemini_contents(messages: list) -> list:
    """Translate a Claude-shaped message list into Gemini contents[]."""
    contents = []
    tool_names_by_id: dict = {}

    for message in messages:
        role = "model" if message.get("role") == "assistant" else "user"
        content = message.get("content")
        parts = []

        if isinstance(content, str):
            parts.append({"text": content})
        else:
            for block in content or []:
                block_type = block.get("type")
                if block_type == "text":
                    if block.get("text"):
                        parts.append({"text": block["text"]})
                elif block_type == "tool_use":
                    tool_names_by_id[block["id"]] = block["name"]
                    function_call = {
                        "id": block["id"],
                        "name": block["name"],
                        "args": block.get("input") or {},
                    }
                    part = {"function_call": function_call}
                    signature = block.get("_gemini_thought_signature")
                    if signature:
                        part["thought_signature"] = signature
                    parts.append(part)
                elif block_type == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    name = tool_names_by_id.get(tool_use_id, tool_use_id)
                    if block.get("is_error"):
                        response = {"error": block.get("content")}
                    else:
                        response = {"content": block.get("content")}
                    parts.append(
                        {
                            "function_response": {
                                "id": tool_use_id,
                                "name": name,
                                "response": response,
                            }
                        }
                    )
                else:
                    # Unknown block type: pass through as text rather than
                    # silently dropping it (matches sanitize_response_
                    # content_for_request.py's own "unknown types pass
                    # through unchanged" posture for the Claude side).
                    parts.append({"text": str(block)})

        contents.append({"role": role, "parts": parts})

    return contents


def gemini_parts_to_message_content(parts: list) -> list:
    """Translate one Gemini response candidate's parts into Claude-shaped content blocks.

    Only text and function_call parts are translated -- this pipeline's
    verify turn loop never sends/expects executable_code, inline_data,
    or other Gemini-specific part types.
    """
    blocks = []
    for part in parts or []:
        if part.get("text"):
            blocks.append({"type": "text", "text": part["text"]})
        elif part.get("function_call"):
            function_call = part["function_call"]
            block = {
                "type": "tool_use",
                "id": function_call.get("id"),
                "name": function_call.get("name"),
                "input": function_call.get("args") or {},
            }
            signature = part.get("thought_signature")
            if signature:
                block["_gemini_thought_signature"] = signature
            blocks.append(block)
    return blocks


def gemini_parts_to_tool_calls(parts: list) -> list:
    """Extract normalized {id, name, input} tool calls from one Gemini candidate's parts."""
    calls = []
    for part in parts or []:
        function_call = part.get("function_call")
        if function_call:
            calls.append(
                {
                    "id": function_call.get("id"),
                    "name": function_call.get("name"),
                    "input": function_call.get("args") or {},
                }
            )
    return calls


class FilterModule:
    def filters(self):
        return {
            "to_gemini_contents": to_gemini_contents,
            "gemini_parts_to_message_content": gemini_parts_to_message_content,
            "gemini_parts_to_tool_calls": gemini_parts_to_tool_calls,
        }
