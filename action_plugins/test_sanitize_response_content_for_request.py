# -*- coding: utf-8 -*-
"""Behavioral tests for sanitize_response_content_for_request.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/
"""
from __future__ import annotations

from sanitize_response_content_for_request import ActionModule, sanitize_response_content_for_request

# A real newer-SDK-shaped response: a tool_use block carrying an extra
# response-only field the request schema rejects.
FIXTURE_CONTENT = [
    {"type": "text", "text": "I'll check that file."},
    {
        "type": "tool_use",
        "id": "toolu_01abc",
        "name": "read_file",
        "input": {"path": "foo.py"},
        "toolset_name": "custom",
    },
]

EXPECTED_RESULT = [
    {"type": "text", "text": "I'll check that file."},
    {"type": "tool_use", "id": "toolu_01abc", "name": "read_file", "input": {"path": "foo.py"}},
]


def test_strips_extra_tool_use_field():
    assert sanitize_response_content_for_request(FIXTURE_CONTENT) == EXPECTED_RESULT


def test_text_block_with_extra_field_is_stripped_too():
    content = [{"type": "text", "text": "hello", "citations": None}]
    assert sanitize_response_content_for_request(content) == [{"type": "text", "text": "hello"}]


def test_unknown_block_type_passed_through_unchanged():
    content = [{"type": "thinking", "thinking": "reasoning...", "signature": "sig123"}]
    assert sanitize_response_content_for_request(content) == content


def test_unknown_block_type_warns(monkeypatch):
    warnings = []
    monkeypatch.setattr("sanitize_response_content_for_request.display.warning", warnings.append)
    sanitize_response_content_for_request([{"type": "thinking", "thinking": "..."}])
    assert len(warnings) == 1
    assert "thinking" in warnings[0]


def test_known_block_type_does_not_warn(monkeypatch):
    warnings = []
    monkeypatch.setattr("sanitize_response_content_for_request.display.warning", warnings.append)
    sanitize_response_content_for_request(FIXTURE_CONTENT)
    assert warnings == []


def test_empty_list_returns_empty_list():
    assert sanitize_response_content_for_request([]) == []


def test_order_and_length_preserved():
    result = sanitize_response_content_for_request(FIXTURE_CONTENT)
    assert len(result) == len(FIXTURE_CONTENT)
    assert [b["type"] for b in result] == [b["type"] for b in FIXTURE_CONTENT]


def test_block_without_extra_fields_is_unchanged():
    content = [{"type": "tool_use", "id": "toolu_02", "name": "search", "input": {"q": "x"}}]
    assert sanitize_response_content_for_request(content) == content


# --- ActionModule wiring smoke test (see strip_verify_index's test file
# for why these are hand-rolled, narrow test doubles) --------------------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "sanitize_response_content_for_request"
        self.async_val = False
        self.check_mode = False


def _run_action_module(content):
    action = ActionModule(
        task=_FakeTask({"content": content}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(FIXTURE_CONTENT)
    assert "failed" not in result
    assert result["content"] == sanitize_response_content_for_request(FIXTURE_CONTENT)


def test_action_module_requires_content_arg():
    action = ActionModule(
        task=_FakeTask({}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
