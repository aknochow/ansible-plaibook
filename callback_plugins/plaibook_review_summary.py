# -*- coding: utf-8 -*-
"""Print the review summary after Ansible and aggregate callbacks finish."""

from __future__ import annotations

import atexit
import os
import sys

from ansible.plugins.callback import CallbackBase

try:
    from plaibook.task_failures import (
        clean_failure_message,
        failure_message_from_result,
        redact_failure_text,
    )
except ImportError:  # pragma: no cover - checkout without the package installed
    import importlib.util

    _sibling = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "plaibook", "task_failures.py")
    )
    _spec = importlib.util.spec_from_file_location("_plaibook_task_failures_summary", _sibling)
    if _spec is None or _spec.loader is None or not os.path.isfile(_sibling):
        raise
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    clean_failure_message = _mod.clean_failure_message
    failure_message_from_result = _mod.failure_message_from_result
    redact_failure_text = _mod.redact_failure_text


DOCUMENTATION = """
    name: plaibook_review_summary
    type: aggregate
    short_description: Print the review summary after the play recap.
    description:
      - Reads the summary fact prepared by review.yml and prints it after
        the recap and other callback output have completed.
      - Prints a sanitized failure fallback if review.yml stops before
        the summary fact is created.
      - Failure text is redacted with the same rules as the task-failure log.
        Exception tracebacks are not copied into the summary.
    requirements: []
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "plaibook_review_summary"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self) -> None:
        super().__init__()
        self._owner_pid = os.getpid()
        self._review_play = False
        self._stats_seen = False
        self._summary = ""
        self._failed_target = ""
        self._failed_task = ""
        self._failure_reason = ""
        atexit.register(self._print_final_summary)

    def v2_playbook_on_play_start(self, play: object) -> None:
        self._review_play = getattr(play, "name", "") == "Code review pipeline"

    def v2_runner_on_ok(self, result: object) -> None:
        if not self._review_play:
            return
        payload = getattr(result, "_result", None) or {}
        facts = payload.get("ansible_facts") or {}
        self._failed_target = facts.get("review_failed_target") or self._failed_target
        summary = facts.get("plaibook_final_review_summary")
        if isinstance(summary, str) and summary.strip():
            self._summary = summary.strip()

    def v2_runner_on_failed(self, result: object, ignore_errors: bool = False) -> None:
        self._capture_failure(result)

    def v2_runner_on_unreachable(self, result: object) -> None:
        self._capture_failure(result)

    def v2_playbook_on_stats(self, stats: object) -> None:
        self._stats_seen = self._review_play

    def _capture_failure(self, result: object) -> None:
        if not self._review_play or self._summary:
            return
        task = getattr(result, "_task", None)
        get_name = getattr(task, "get_name", None)
        self._failed_task = (get_name() if callable(get_name) else "") or "Unknown task"
        payload = getattr(result, "_result", None) or {}
        # msg only. exception, stdout, and stderr are not copied.
        self._failure_reason = clean_failure_message(failure_message_from_result(payload))

    def _print_final_summary(self) -> None:
        if os.getpid() != self._owner_pid or not self._stats_seen:
            return

        if self._summary:
            summary = self._summary
        else:
            summary = "\n".join(
                (
                    "=== Review Summary ===",
                    "Run status: FAILED",
                    f"Failed target: {self._failed_target or 'Unknown target'}",
                    f"Failed task: {self._failed_task or 'Review summary was not produced'}",
                    f"Reason: {self._failure_reason or 'The review stopped before summary details were recorded.'}",
                    "Findings report: not created (the review failed before results were persisted).",
                )
            )

        redacted = redact_failure_text(summary)
        sys.stdout.write(f"\n{redacted}\n")
        sys.stdout.flush()
