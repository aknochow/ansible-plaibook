# -*- coding: utf-8 -*-
"""Jinja filters for the OpenAI Chat Completions wire format.

The review role keeps one provider-neutral, Claude-shaped conversation so the
existing tool runner can execute read-only tools in Ansible.  OpenAI uses a
different representation for assistant tool calls and tool results, so this
module translates at the provider boundary instead of duplicating the review
loop.
"""
from __future__ import annotations

import json


def to_openai_messages(messages: list) -> list:
    """Translate the role's Claude-shaped conversation to Chat Completions messages."""
    result = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        blocks = content or []
        text_parts = []
        tool_calls = []
        tool_results = []
        for block in blocks:
            block_type = block.get("type")
            if block_type == "text" and block.get("text"):
                text_parts.append(block["text"])
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input") or {}, separators=(",", ":")),
                        },
                    }
                )
            elif block_type == "tool_result":
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id"),
                        "content": str(block.get("content", "")),
                    }
                )
            else:
                text_parts.append(str(block))

        if role == "assistant":
            assistant = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            result.append(assistant)
        elif tool_results:
            result.extend(tool_results)
        else:
            result.append({"role": role, "content": "\n".join(text_parts)})

    return result


def to_openai_tools(tools: list) -> list:
    """Translate Claude-shaped tool definitions to OpenAI function tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in tools
    ]


def openai_response_to_message_content(text: str, tool_calls: list) -> list:
    """Translate a flattened collection response into Claude-shaped content blocks."""
    blocks = []
    if text:
        blocks.append({"type": "text", "text": text})
    for call in tool_calls or []:
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id"),
                "name": call.get("name"),
                "input": call.get("args") or {},
            }
        )
    return blocks


def openai_tool_calls_with_input(tool_calls: list) -> list:
    """Add the shared loop's ``input`` alias to the collection's ``args`` calls."""
    return [dict(call, input=call.get("args") or {}) for call in (tool_calls or [])]


def openai_reasoning_tokens(response: dict) -> int:
    """Return provider-reported hidden reasoning tokens from a raw response."""
    if not isinstance(response, dict):
        return 0
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return 0
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        return 0
    value = details.get("reasoning_tokens", 0)
    return int(value) if isinstance(value, (int, float)) else 0


class FilterModule:
    def filters(self):
        return {
            "to_openai_messages": to_openai_messages,
            "to_openai_tools": to_openai_tools,
            "openai_response_to_message_content": openai_response_to_message_content,
            "openai_tool_calls_with_input": openai_tool_calls_with_input,
            "openai_reasoning_tokens": openai_reasoning_tokens,
        }
