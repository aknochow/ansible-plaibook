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


def test_xai_stop_reason_accepts_the_sdk_names():
    from openai_conversation import xai_stop_reason

    assert xai_stop_reason("REASON_STOP") == "end_turn"
    assert xai_stop_reason("FINISH_REASON_STOP") == "end_turn"
    assert xai_stop_reason("stop") == "end_turn"
    assert xai_stop_reason("REASON_STOP", True) == "tool_use"
    assert xai_stop_reason("length") == "length"


_RST_STREAM = (
    "<_MultiThreadedRendezvous of RPC that terminated with:\n"
    "\tstatus = StatusCode.INTERNAL\n"
    '\tdetails = "Received RST_STREAM with error code 2"\n'
    '\tdebug_error_string = "UNKNOWN:Error received from peer ipv4:192.0.2.1:443 {grpc_status:13}"\n>'
)


def test_lens_retry_reads_the_finished_job_not_failed():
    from openai_conversation import xai_lens_needs_transport_retry as needs_retry

    # failed_when: false on the wait leaves failed False on a real job.
    assert needs_retry({"finished": 1, "failed": False, "msg": _RST_STREAM})
    assert needs_retry({"finished": 1, "failed": False, "msg": 'status = StatusCode.UNAVAILABLE details = "x"'})
    assert needs_retry({"finished": 1, "attempts": [{"usage": None, "error": _RST_STREAM}]})
    assert not needs_retry({"finished": 0, "msg": _RST_STREAM})
    assert not needs_retry({"finished": 1, "structured": {"findings": []}, "msg": _RST_STREAM})
    assert not needs_retry(None)


def test_lens_retry_refuses_auth_invalid_quota_and_timeout():
    from openai_conversation import xai_lens_needs_transport_retry as needs_retry

    for msg in (
        'status = StatusCode.UNAUTHENTICATED details = "bad key"',
        'status = StatusCode.PERMISSION_DENIED details = "no access"',
        'status = StatusCode.INVALID_ARGUMENT details = "bad schema"',
        'status = StatusCode.RESOURCE_EXHAUSTED details = "out of credits"',
        "Error code: 429 - rate_limit_error",
        "The xAI stream exceeded the client timeout of 1800 seconds before the response finished.",
        "Timeout exceeded",
        "widget exploded",
        "",
    ):
        assert not needs_retry({"finished": 1, "failed": False, "msg": msg}), msg


def test_provider_error_message_drops_keys_prompt_and_debug():
    from openai_conversation import xai_provider_error_message as clean

    message = clean(_RST_STREAM)
    assert "RST_STREAM" in message
    assert "192.0.2.1" not in message
    assert "debug_error_string" not in message
    assert "abcdef123456" not in clean("rejected key xai-abcdef123456")
    assert "abcdef123456" not in clean("api_key=abcdef123456 was rejected")
    assert "abcdef123456" not in clean("Authorization: Bearer abcdef123456")
    prompt = "short\nThis exact prompt line must stay private\n"
    message = clean("bad input: This exact prompt line must stay private", [prompt])
    assert "must stay private" not in message
    assert message.startswith("bad input:")
    assert len(clean("x" * 5000)) < 600


def test_provider_error_message_hides_the_environment_key(monkeypatch):
    from openai_conversation import xai_provider_error_message as clean

    monkeypatch.setenv("XAI_API_KEY", "not-a-real-env-key")
    assert "not-a-real-env-key" not in clean("sent not-a-real-env-key upstream")


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
