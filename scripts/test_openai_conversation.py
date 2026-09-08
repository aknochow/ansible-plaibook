import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "filter_plugins"))

from openai_conversation import (
    openai_reasoning_tokens,
    openai_response_to_message_content,
    openai_tool_calls_with_input,
    to_openai_messages,
    to_openai_tools,
)


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
    assert openai_tool_calls_with_input(calls) == [
        {"id": "call_1", "name": "report_findings", "args": {"findings": []}, "input": {"findings": []}}
    ]


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
