# -*- coding: utf-8 -*-
"""Decide whether a verify provider fail_json is one retryable attempt.

Claude, Claude CLI, Gemini, and OpenAI fail_json with a message. They
do not set status: error, and they do not mark the error class. A
message-only return is retryable only when the text is a known
transient outage. Authentication, authorization, invalid-request, and
any unrecognized message are not retryable: the caller re-raises them.
"""

from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

# Permanent failures win even if the text also mentions a status code
# that would otherwise look transient.
_PERMANENT = (
    re.compile(r"authentication|unauthorized|\b401\b", re.IGNORECASE),
    re.compile(r"permission|forbidden|\b403\b", re.IGNORECASE),
    re.compile(r"invalid[_ ]request|\b400\b", re.IGNORECASE),
    re.compile(r"(?:api[_ ]?key|auth_token).{0,80}required", re.IGNORECASE),
    re.compile(r"(?:SDK|package) is required", re.IGNORECASE),
    re.compile(r"Unknown (?:provider|backend)\b", re.IGNORECASE),
    re.compile(r"mutually exclusive", re.IGNORECASE),
    re.compile(r"must use http", re.IGNORECASE),
)

_RETRYABLE = (
    re.compile(r"overloaded", re.IGNORECASE),
    re.compile(r"rate[ _-]?limit", re.IGNORECASE),
    re.compile(r"\b429\b"),
    re.compile(r"\b500\b"),
    re.compile(r"\b502\b"),
    re.compile(r"\b503\b"),
    re.compile(r"\b529\b"),
    re.compile(r"timed out|\btimeout\b", re.IGNORECASE),
    re.compile(r"internal server error", re.IGNORECASE),
    re.compile(r"server had an error", re.IGNORECASE),
    re.compile(r"temporarily unavailable|service unavailable", re.IGNORECASE),
    re.compile(r"connection (?:error|reset|refused|aborted)", re.IGNORECASE),
)


def provider_failure_is_retryable(msg: str) -> bool:
    """True only for a known transient provider message."""
    text = msg or ""
    if any(pattern.search(text) for pattern in _PERMANENT):
        return False
    return any(pattern.search(text) for pattern in _RETRYABLE)


class ActionModule(ActionBase):
    """Return retryable=true when msg is a known transient provider failure."""

    _requires_connection = False

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, object] | None = None,
    ) -> dict[str, object]:
        result = super().run(tmp, task_vars)
        del tmp
        msg = self._task.args.get("msg")
        if not isinstance(msg, str):
            msg = "" if msg is None else str(msg)
        result["retryable"] = provider_failure_is_retryable(msg)
        return result
