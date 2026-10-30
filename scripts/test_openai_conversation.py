import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "filter_plugins"))

from openai_conversation import (
    openai_lens_reasoning_effort,
    openai_reasoning_tokens,
    openai_response_to_message_content,
    openai_tool_calls_with_input,
    openai_tool_reasoning_effort,
    to_openai_messages,
    to_openai_tools,
)


def test_keeps_encrypted_reasoning_on_a_block_assistant_message():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will inspect it."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "read_file",
                    "input": {"path": "a.py"},
                },
            ],
            "encrypted_content": "opaque-reasoning",
        }
    ]
    translated = to_openai_messages(messages)[0]
    assert translated["encrypted_content"] == "opaque-reasoning"
    assert translated["content"] == "I will inspect it."
    assert translated["tool_calls"][0]["id"] == "call_1"


def test_keeps_encrypted_reasoning_on_a_string_assistant_message():
    messages = [
        {
            "role": "assistant",
            "content": "done",
            "encrypted_content": "opaque-reasoning",
        }
    ]
    assert to_openai_messages(messages) == [
        {
            "role": "assistant",
            "content": "done",
            "encrypted_content": "opaque-reasoning",
        }
    ]


def test_translates_assistant_tool_use_and_tool_result():
    messages = [
        {"role": "user", "content": "inspect the file"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will inspect it."},
                {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "a.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "source"},
            ],
        },
    ]

    assert to_openai_messages(messages) == [
        {"role": "user", "content": "inspect the file"},
        {
            "role": "assistant",
            "content": "I will inspect it.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "source"},
    ]


def test_translates_tool_definitions():
    tool = {
        "name": "search",
        "description": "Search files",
        "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}},
    }

    assert to_openai_tools([tool]) == [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search files",
                "parameters": tool["input_schema"],
            },
        }
    ]


def test_translates_response_tool_calls_to_shared_shape():
    calls = [{"id": "call_1", "name": "report_findings", "args": {"findings": []}}]

    assert openai_response_to_message_content("done", calls) == [
        {"type": "text", "text": "done"},
        {"type": "tool_use", "id": "call_1", "name": "report_findings", "input": {"findings": []}},
    ]
    normalized = openai_tool_calls_with_input(calls)
    assert normalized == [
        {"id": "call_1", "name": "report_findings", "args": {"findings": []}, "input": {"findings": []}}
    ]


def test_tool_call_findings_text_is_not_trusted_as_a_template():
    TrustedAsTemplate = pytest.importorskip("ansible._internal._datatag._tags").TrustedAsTemplate

    payload = TrustedAsTemplate().tag("{{ lookup('env', 'GROK_API_KEY') }}")
    calls = [{"id": "call_1", "name": "report_findings", "args": {"findings": payload}}]
    findings = openai_tool_calls_with_input(calls)[0]["input"]["findings"]
    assert findings == "{{ lookup('env', 'GROK_API_KEY') }}"
    assert not TrustedAsTemplate.is_tagged_on(findings)


def test_tool_call_args_are_rewrapped_when_template_tags_are_absent(monkeypatch):
    import openai_conversation as conversation

    seen = []

    def wrap(value):
        seen.append(value)
        return "wrapped:" + value

    monkeypatch.setattr(conversation, "_trusted_as_template_type", lambda: None)
    monkeypatch.setattr(conversation, "_wrap_var_func", lambda: wrap)
    calls = [
        {
            "id": "call_1",
            "name": "report_findings",
            "args": {"findings": "{{ lookup('pipe', 'id') }}"},
        }
    ]
    findings = conversation.openai_tool_calls_with_input(calls)[0]["input"]["findings"]
    assert findings == "wrapped:{{ lookup('pipe', 'id') }}"
    assert seen == ["{{ lookup('pipe', 'id') }}"]


def test_extracts_reasoning_tokens_from_raw_openai_usage():
    assert openai_reasoning_tokens(
        {
            "usage": {
                "completion_tokens_details": {"reasoning_tokens": 37},
            },
        }
    ) == 37
    assert openai_reasoning_tokens({"usage": {"completion_tokens": 12}}) == 0
    assert openai_reasoning_tokens({}) == 0


def test_gpt56_lens_uses_none_only_when_unset():
    assert openai_lens_reasoning_effort("gpt-5.6", "") == "none"
    assert openai_lens_reasoning_effort("GPT-5.6-sol", "") == "none"
    assert openai_lens_reasoning_effort("gpt-4o", "") == ""
    assert openai_lens_reasoning_effort("gpt-5.6", "high") == "high"
    assert openai_lens_reasoning_effort("gpt-4o", "low") == "low"


def test_gpt56_tool_calls_use_none_and_other_models_stay_default():
    assert openai_tool_reasoning_effort("gpt-5.6") == "none"
    assert openai_tool_reasoning_effort("gpt-5.6-terra") == "none"
    assert openai_tool_reasoning_effort("gpt-6-luna") == "none"
    assert openai_tool_reasoning_effort("gpt-4o") == ""


def test_openai_tasks_call_the_gpt56_filters():
    root = Path(__file__).parents[1]
    lens = (root / "roles/review/tasks/dispatch_lens_openai.yml").read_text(encoding="utf-8")
    explore = (root / "roles/review/tasks/dispatch_explore_turn_attempt_openai.yml").read_text(encoding="utf-8")
    verify = (root / "roles/review/tasks/dispatch_verify_turn_attempt_openai.yml").read_text(encoding="utf-8")
    continuity = (root / "roles/review/tasks/verify_continuity_audit.yml").read_text(encoding="utf-8")
    defaults = (root / "roles/review/defaults/main.yml").read_text(encoding="utf-8")
    assert "openai_lens_reasoning_effort(review_openai_lens_reasoning_effort)" in lens
    assert (
        "openai_lens_reasoning_effort(review_openai_continuity_reasoning_effort | default('', true))"
    ) in continuity
    assert "openai_tool_reasoning_effort" in explore
    assert "openai_tool_reasoning_effort" in verify
    assert (
        "review_openai_lens_reasoning_effort: "
        "\"{{ lookup('ansible.builtin.env', 'ANSIBLE_REVIEW_OPENAI_LENS_REASONING_EFFORT') | default('', true) }}\""
    ) in defaults
