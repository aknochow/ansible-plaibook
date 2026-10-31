# -*- coding: utf-8 -*-
"""Decide whether a verify provider fail_json is one retryable attempt.

Claude, Claude CLI, Gemini, and OpenAI fail_json with a message. They
do not set status: error, and they do not mark the error class. A
message-only return is retryable only when the text is a known
transient outage. Authentication, authorization, invalid-request, and
any unrecognized message are not retryable: the caller re-raises them.

xAI fails with the gRPC error text instead of an HTTP status. Its
transient forms are StatusCode.INTERNAL, StatusCode.UNAVAILABLE, and a
dropped stream (RST_STREAM). Other gRPC codes stay permanent even when
their details mention a dropped connection.

provider_failure_class also names the kind of failure. A verify or
explore attempt retries every class in _RETRYABLE_CLASSES. A lens is one
long, costly call and retries only "transient": a timeout would spend
the same budget again, and a quota error will not clear in a retry.
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
    re.compile(
        r"StatusCode\.(?:UNAUTHENTICATED|PERMISSION_DENIED|INVALID_ARGUMENT"
        r"|RESOURCE_EXHAUSTED|FAILED_PRECONDITION|NOT_FOUND|OUT_OF_RANGE|UNIMPLEMENTED)\b"
    ),
)

_QUOTA = (
    re.compile(r"rate[ _-]?limit", re.IGNORECASE),
    re.compile(r"\b429\b"),
)

# Checked before _TIMEOUT: gRPC can word an UNAVAILABLE as a connect timeout.
_GRPC_TRANSPORT = (
    re.compile(r"RST_STREAM"),
    re.compile(r"StatusCode\.(?:INTERNAL|UNAVAILABLE)\b"),
)

_TIMEOUT = (
    re.compile(r"timed out|\btimeout\b", re.IGNORECASE),
)

_TRANSIENT = (
    re.compile(r"overloaded", re.IGNORECASE),
    re.compile(r"\b500\b"),
    re.compile(r"\b502\b"),
    re.compile(r"\b503\b"),
    re.compile(r"\b529\b"),
    re.compile(r"internal server error", re.IGNORECASE),
    re.compile(r"server had an error", re.IGNORECASE),
    re.compile(r"temporarily unavailable|service unavailable", re.IGNORECASE),
    re.compile(r"connection (?:error|reset|refused|aborted)", re.IGNORECASE),
)


# gRPC appends a debug string with the peer address and a timestamp. Its
# digits are not status codes, so a fraction of .401 must not read as a 401.
_GRPC_DEBUG = re.compile(r"debug_error_string\s*=.*", re.DOTALL)

_RETRYABLE_CLASSES = frozenset(("quota", "timeout", "transient"))


def provider_failure_class(msg: str) -> str:
    """Return permanent, quota, timeout, transient, or unknown for a message."""
    text = _GRPC_DEBUG.sub("", msg or "")
    if any(pattern.search(text) for pattern in _PERMANENT):
        return "permanent"
    if any(pattern.search(text) for pattern in _QUOTA):
        return "quota"
    if any(pattern.search(text) for pattern in _GRPC_TRANSPORT):
        return "transient"
    if any(pattern.search(text) for pattern in _TIMEOUT):
        return "timeout"
    if any(pattern.search(text) for pattern in _TRANSIENT):
        return "transient"
    return "unknown"


def provider_failure_is_retryable(msg: str) -> bool:
    """True only for a known transient provider message."""
    return provider_failure_class(msg) in _RETRYABLE_CLASSES


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
        result["failure_class"] = provider_failure_class(msg)
        result["retryable"] = result["failure_class"] in _RETRYABLE_CLASSES
        return result
