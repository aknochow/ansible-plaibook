# -*- coding: utf-8 -*-
"""The post-recap summary must not reprint unsanitized failure text."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from plaibook.task_failures import failure_message_from_result, redact_failure_text


def _callback_module():
    path = Path(__file__).resolve().parents[1] / "callback_plugins" / "plaibook_review_summary.py"
    spec = importlib.util.spec_from_file_location("plaibook_review_summary_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Task:
    def get_name(self) -> str:
        return "Call the provider"


class _Result:
    def __init__(self, payload: dict) -> None:
        self._result = payload
        self._task = _Task()


def test_failure_message_ignores_exception_text():
    secret = "exception-only-secret"
    message = failure_message_from_result(
        {"exception": f"Traceback: {secret}", "stdout": secret}
    )
    assert secret not in message
    assert message == "task failed"


def test_redact_failure_text_masks_bearer_and_prefix_tokens():
    bearer = "bearer-token-value"
    token = "ghp_" + "abcdefghijklmnop"
    text = redact_failure_text(
        "boom Authorization: Bearer "
        + bearer
        + " and "
        + token
        + "\nFindings report: /tmp/findings.md"
    )
    assert bearer not in text
    assert token not in text
    assert "Authorization: Bearer ***" in text
    assert "Findings report: /tmp/findings.md" in text


def test_callback_fallback_does_not_copy_exception_or_bearer(capsys):
    module = _callback_module()
    callback = module.CallbackModule()
    callback._review_play = True
    bearer = "bearer-token-value"
    leaked = "exception-body-secret"
    callback._capture_failure(
        _Result(
            {
                "msg": "provider failed Authorization: Bearer " + bearer,
                "exception": "Traceback includes " + leaked,
            }
        )
    )
    assert bearer not in callback._failure_reason
    assert leaked not in callback._failure_reason
    assert "Authorization: Bearer ***" in callback._failure_reason

    callback._stats_seen = True
    callback._summary = ""
    callback._print_final_summary()
    printed = capsys.readouterr().out
    assert bearer not in printed
    assert leaked not in printed
    assert "Run status: FAILED" in printed


def test_callback_redacts_a_summary_fact_that_already_contains_a_token(capsys):
    module = _callback_module()
    callback = module.CallbackModule()
    callback._review_play = True
    callback._stats_seen = True
    token = "ghp_" + "abcdefghijklmnop"
    callback._summary = (
        "=== Review Summary ===\n"
        "Run status: FAILED\n"
        "Failure reason: clone failed " + token
    )
    callback._print_final_summary()
    printed = capsys.readouterr().out
    assert token not in printed
    assert "Run status: FAILED" in printed
