"""The xAI review family calls aknochow.xai.chat, not the OpenAI shim."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "roles" / "review" / "tasks"


def _text(name: str) -> str:
    return (TASKS / name).read_text(encoding="utf-8")


def test_xai_lens_uses_the_xai_sdk_module():
    lens = _text("dispatch_lens_xai.yml")
    assert "aknochow.xai.chat:" in lens
    assert "aknochow.openai.chat" not in lens
    assert "response_schema:" in lens
    assert "max_tokens:" in lens
    assert "review_xai_lens_reasoning_effort" in lens
    assert "(last_run_id | default('review')) ~ '-security'" in lens
    assert "(last_run_id | default('review')) ~ '-review'" in lens
    assert 'conversation_id: "{{ last_run_id | default(omit) }}"' not in lens
    assert "(xai_timeout | float + 60)" in lens
    assert "failed_when: false" in lens
    assert "xai_lens_needs_transport_retry" in lens
    preflight = _text("provider_preflight.yml")
    assert "doc.options.conversation_id" in preflight
    defaults = (ROOT / "roles" / "review" / "defaults" / "main.yml").read_text(encoding="utf-8")
    assert "default(1800, true)" in defaults
    explore = _text("dispatch_explore_turn_attempt_xai.yml")
    assert "xai_stop_reason" in explore


def test_xai_explore_and_verify_keep_openai_shape_filters():
    explore = _text("dispatch_explore_turn_attempt_xai.yml")
    verify = _text("dispatch_verify_turn_attempt_xai.yml")
    for text in (explore, verify):
        assert "aknochow.xai.chat" in text
        assert "aknochow.openai.chat" not in text
        assert "to_openai_messages" in text
        assert "to_openai_tools" in text
        assert "openai_tool_calls_with_input" in text
        assert "openai_response_to_message_content" in text


def test_forced_explore_turn_requires_report_findings():
    explore = _text("dispatch_explore_turn_attempt_xai.yml")
    assert "forced last turn did not call report_findings" in explore
    assert "explore_turn_result.stop_reason != 'tool_use'" not in explore


def test_family_list_includes_xai():
    config = (ROOT / "plaibook" / "config.py").read_text(encoding="utf-8")
    assert '"xai"' in config
    assert "GROK_API_KEY" in config
