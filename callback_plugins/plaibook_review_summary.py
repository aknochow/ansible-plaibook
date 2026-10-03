# -*- coding: utf-8 -*-
"""Print the review summary after Ansible and aggregate callbacks finish."""

from __future__ import annotations

import atexit
import os
import re
import sys

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
    name: plaibook_review_summary
    type: aggregate
    short_description: Print the review summary after the play recap.
    description:
      - Reads the summary fact prepared by review.yml and prints it after
        the recap and other callback output have completed.
      - Prints a sanitized failure fallback if review.yml stops before
        the summary fact is created.
    requirements: []
"""

_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_CREDENTIALS = re.compile(r"://[^/@\s]+@")
_URL_TOKEN = re.compile(r"([?&](?:token|access_token|private_token)=)[^&\s]*", re.IGNORECASE)


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "plaibook_review_summary"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self._owner_pid = os.getpid()
        self._review_play = False
        self._stats_seen = False
        self._summary = ""
        self._failed_target = ""
        self._failed_task = ""
        self._failure_reason = ""
        atexit.register(self._print_final_summary)

    def v2_playbook_on_play_start(self, play):
        self._review_play = getattr(play, "name", "") == "Code review pipeline"

    def v2_runner_on_ok(self, result):
        if not self._review_play:
            return
        payload = getattr(result, "_result", None) or {}
        facts = payload.get("ansible_facts") or {}
        self._failed_target = facts.get("review_failed_target") or self._failed_target
        summary = facts.get("plaibook_final_review_summary")
        if isinstance(summary, str) and summary.strip():
            self._summary = summary.strip()

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._capture_failure(result)

    def v2_runner_on_unreachable(self, result):
        self._capture_failure(result)

    def v2_playbook_on_stats(self, stats):
        self._stats_seen = self._review_play

    def _capture_failure(self, result):
        if not self._review_play or self._summary:
            return
        task = getattr(result, "_task", None)
        get_name = getattr(task, "get_name", None)
        self._failed_task = (get_name() if callable(get_name) else "") or "Unknown task"
        payload = getattr(result, "_result", None) or {}
        reason = payload.get("msg") or payload.get("exception") or "No failure detail was returned."
        reason = _URL_CREDENTIALS.sub("://***@", str(reason))
        reason = _URL_TOKEN.sub(r"\1***", reason)
        self._failure_reason = reason

    def _print_final_summary(self):
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

        summary = _ANSI_ESCAPE.sub("", summary)
        summary = _CONTROL_CHARACTERS.sub("", summary)
        summary = _URL_CREDENTIALS.sub("://***@", summary)
        summary = _URL_TOKEN.sub(r"\1***", summary)
        sys.stdout.write(f"\n{summary}\n")
        sys.stdout.flush()
