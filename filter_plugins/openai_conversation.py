# -*- coding: utf-8 -*-
"""Jinja filters for the OpenAI Chat Completions wire format.

The review role keeps one provider-neutral, Claude-shaped conversation so the
existing tool runner can execute read-only tools in Ansible.  OpenAI uses a
different representation for assistant tool calls and tool results, so this
module translates at the provider boundary instead of duplicating the review
loop.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

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


_failure_classifier = None


def _provider_failure_class(msg: str) -> str:
    """Use the verify classifier, so xAI lens, explore, and verify share one list."""
    global _failure_classifier
    if _failure_classifier is None:
        path = Path(__file__).resolve().parents[1] / "action_plugins" / "classify_verify_provider_failure.py"
        spec = importlib.util.spec_from_file_location("_plaibook_provider_failure", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _failure_classifier = module.provider_failure_class
    return _failure_classifier(msg)


def _xai_error_text(result: dict) -> str:
    """The module's own error: msg, else the error on its last attempt."""
    msg = result.get("msg")
    if msg:
        return str(msg)
    attempts = result.get("attempts")
    if isinstance(attempts, list) and attempts and isinstance(attempts[-1], dict):
        return str(attempts[-1].get("error") or "")
    return ""


def xai_lens_needs_transport_retry(result: dict) -> bool:
    """True when one lens died on a dropped stream and should be sent once more.

    The wait task's failed_when: false rewrites ``failed``, so this reads what
    the job left: it finished, it has no structured output, and its message is
    a transient failure. Auth, invalid-request, quota, and timeout are not.
    """
    if not isinstance(result, dict) or not result.get("finished"):
        return False
    if isinstance(result.get("structured"), dict):
        return False
    return _provider_failure_class(_xai_error_text(result)) == "transient"


_XAI_KEY_PATTERNS = (
    re.compile(r"\bxai-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
)
_XAI_KEY_ASSIGNMENT = re.compile(r"\b(api[_-]?key|auth[_-]?token)(\s*[=:]\s*)[^\s,;]+", re.IGNORECASE)
_GRPC_DEBUG_TEXT = re.compile(r"\s*debug_error_string\s*=.*", re.DOTALL)
_XAI_ERROR_LIMIT = 500


def xai_provider_error_message(msg: str, redact=None) -> str:
    """Make provider error text safe to show in a failure message.

    Removes API keys, gRPC's peer debug string, and any line of the prompts in
    ``redact``. The text is from the provider, so it stays untemplatable.
    """
    text = _GRPC_DEBUG_TEXT.sub("", str(msg or ""))
    for name in ("XAI_API_KEY", "GROK_API_KEY"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "<api_key>")
    for pattern in _XAI_KEY_PATTERNS:
        text = pattern.sub("<api_key>", text)
    text = _XAI_KEY_ASSIGNMENT.sub(r"\1\2<api_key>", text)
    for prompt in redact or []:
        for line in str(prompt).splitlines():
            line = line.strip()
            # Short lines are headings and common words, not prompt content.
            if len(line) >= 20 and line in text:
                text = text.replace(line, "<prompt text>")
    text = " ".join(text.split())
    if len(text) > _XAI_ERROR_LIMIT:
        text = text[:_XAI_ERROR_LIMIT] + "..."
    untag_type = _trusted_as_template_type()
    return _copy_model_value(text, untag_type, None if untag_type is not None else _wrap_var_func())


def _usage_int(usage: dict, key: str) -> int:
    value = usage.get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def xai_usage_normalized(usage: dict) -> dict:
    """Translate aknochow.xai.chat usage to the pipeline's normalized shape."""
    if not isinstance(usage, dict):
        usage = {}
    cached = _usage_int(usage, "cached_tokens")
    return {
        "input_tokens": max(_usage_int(usage, "prompt_tokens") - cached, 0),
        "output_tokens": _usage_int(usage, "completion_tokens"),
        "cache_write_tokens": 0,
        "cache_read_tokens": cached,
        "thinking_tokens": _usage_int(usage, "reasoning_tokens"),
        "total_tokens": _usage_int(usage, "total_tokens"),
        "cost_in_usd_ticks": _usage_int(usage, "cost_in_usd_ticks"),
    }


def _xai_billed_usages(result: dict) -> list:
    """Every usage one job billed, oldest first.

    ``attempts`` has one entry per module attempt, and its last entry is the
    response top-level ``usage`` also reports, so only one of the two counts.
    A collection without ``attempts`` has only top-level ``usage``.
    """
    attempts = result.get("attempts")
    if isinstance(attempts, list):
        usages = [a["usage"] for a in attempts if isinstance(a, dict) and isinstance(a.get("usage"), dict)]
        if usages:
            return usages
    usage = result.get("usage")
    return [usage] if isinstance(usage, dict) and usage else []


def xai_lens_usage_calls(results: list, target: str, lens: str, model: str) -> list:
    """pipeline_agent_calls entries for one lens's jobs, first dispatch and retry.

    An attempt with no usage is left out. It billed nothing, and a zero entry
    would mark a model priced only by ticks as unpriced.
    """
    calls = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        response = result.get("response")
        served = response.get("model") if isinstance(response, dict) else None
        for usage in _xai_billed_usages(result):
            calls.append(
                {
                    "target": target,
                    "lens": lens,
                    "model": served or model,
                    "usage": xai_usage_normalized(usage),
                    "attempt": len(calls) + 1,
                }
            )
    return calls


def xai_lens_final_result(first: dict, retry: dict) -> dict:
    """The retry's result when one ran, so the last error is the one reported."""
    if isinstance(retry, dict) and retry and not retry.get("skipped"):
        return retry
    return first


def xai_lens_failure_reason(result: dict, redact=None) -> str:
    """Empty when the lens has structured output. Otherwise a sanitized reason."""
    if not isinstance(result, dict):
        return "the lens returned no result"
    if isinstance(result.get("structured"), dict):
        return ""
    if not result.get("finished"):
        return "the job did not finish inside the wait budget"
    error = _xai_error_text(result)
    if error:
        return xai_provider_error_message(error, redact)
    reason = re.sub(r"[^A-Za-z0-9_.-]", "", str(result.get("finish_reason") or "unknown"))
    return f"no structured output despite response_schema being set (finish_reason={reason})"


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
            "xai_provider_error_message": xai_provider_error_message,
            "xai_usage_normalized": xai_usage_normalized,
            "xai_lens_usage_calls": xai_lens_usage_calls,
            "xai_lens_final_result": xai_lens_final_result,
            "xai_lens_failure_reason": xai_lens_failure_reason,
        }
