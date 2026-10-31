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
import re

_GPT56 = re.compile(r"^gpt-5\.6(?:$|-)")
_GPT6_LUNA = re.compile(r"^gpt-6-luna(?:$|-)")


def _copy_encrypted(item: dict, message: dict) -> dict:
    """Keep a prior turn's encrypted reasoning on the translated message."""
    encrypted = message.get("encrypted_content")
    if isinstance(encrypted, str) and encrypted:
        item["encrypted_content"] = encrypted
    return item


def to_openai_messages(messages: list) -> list:
    """Translate the role's Claude-shaped conversation to Chat Completions messages."""
    result = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if isinstance(content, str):
            result.append(_copy_encrypted({"role": role, "content": content}, message))
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
            result.append(_copy_encrypted(assistant, message))
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


def _trusted_as_template_type():
    """Return Ansible 2.19's template tag, or None on ansible-core 2.16."""
    try:
        from ansible._internal._datatag._tags import TrustedAsTemplate
    except ImportError:
        return None
    return TrustedAsTemplate


def _wrap_var_func():
    """Return ansible-core 2.16's unsafe wrapper, when that module exists."""
    try:
        from ansible.utils.unsafe_proxy import wrap_var
    except ImportError:
        return None
    return wrap_var


def _copy_model_value(value, untag_type, wrap):
    """Copy model text without leaving it templatable on the controller.

    Ansible 2.19 re-templates a string tagged ``TrustedAsTemplate``, so those
    strings are untagged. Ansible 2.16 has no such tag; ``wrap_var`` keeps the
    copy unsafe after ``dict()`` drops the original wrapper.
    """
    if isinstance(value, str):
        if untag_type is not None:
            return untag_type.untag(value)
        if wrap is not None:
            return wrap(value)
        return value
    if isinstance(value, dict):
        return {key: _copy_model_value(item, untag_type, wrap) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_model_value(item, untag_type, wrap) for item in value]
    return value


def openai_tool_calls_with_input(tool_calls: list) -> list:
    """Add the shared loop's ``input`` alias to the collection's ``args`` calls.

    ``args`` is model-controlled text. Copying it into a new dict drops
    Ansible's unsafe wrapper, and the next ``{{ input.findings }}`` then
    templates ``{{ }}`` inside that text on the controller.
    """
    untag_type = _trusted_as_template_type()
    wrap = None if untag_type is not None else _wrap_var_func()
    wrapped = []
    for call in tool_calls or []:
        args = call.get("args") or {}
        wrapped.append(dict(call, input=_copy_model_value(args, untag_type, wrap)))
    return wrapped


def openai_lens_reasoning_effort(model: str, configured: str = "") -> str:
    """Reasoning effort for a structured lens call.

    An operator value wins. gpt-5.6 Chat Completions rejects function tools
    unless the effort is ``none``. Every other model keeps the API default,
    which is an empty string the task omits.
    """
    chosen = (configured or "").strip()
    if chosen:
        return chosen
    if _GPT56.match((model or "").strip().lower()):
        return "none"
    return ""


def openai_tool_reasoning_effort(model: str) -> str:
    """Reasoning effort for a Chat Completions call that sends function tools.

    gpt-5.6 and gpt-6-luna require ``none``. Other models stay on the API default.
    """
    name = (model or "").strip().lower()
    if _GPT56.match(name) or _GPT6_LUNA.match(name):
        return "none"
    return ""


_XAI_END_REASONS = {"stop", "REASON_STOP", "FINISH_REASON_STOP"}


def xai_stop_reason(finish_reason: str, has_tool_calls: bool = False) -> str:
    """Map an xAI finish reason onto the shared explore/verify stop_reason.

    The SDK has returned ``stop``, ``REASON_STOP``, and ``FINISH_REASON_STOP``
    for a normal end. A tool call is ``tool_use`` regardless of that string.
    """
    if has_tool_calls:
        return "tool_use"
    reason = finish_reason or ""
    if reason in _XAI_END_REASONS:
        return "end_turn"
    return reason or "unknown"


def xai_lens_needs_transport_retry(result: dict) -> bool:
    """True when one lens died on a dropped stream and should be sent once more."""
    if not isinstance(result, dict) or not result.get("failed"):
        return False
    msg = str(result.get("msg") or result.get("stderr") or "")
    return any(token in msg for token in ("RST_STREAM", "StatusCode.INTERNAL", "UNAVAILABLE"))


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
            "openai_lens_reasoning_effort": openai_lens_reasoning_effort,
            "openai_tool_reasoning_effort": openai_tool_reasoning_effort,
            "openai_reasoning_tokens": openai_reasoning_tokens,
            "xai_stop_reason": xai_stop_reason,
            "xai_lens_needs_transport_retry": xai_lens_needs_transport_retry,
        }
