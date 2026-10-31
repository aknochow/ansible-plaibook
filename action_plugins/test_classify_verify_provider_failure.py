# -*- coding: utf-8 -*-
"""Unit tests for the verify provider failure allowlist."""

from classify_verify_provider_failure import provider_failure_class, provider_failure_is_retryable

# The shape of str() on the grpc errors xai-sdk chat.stream raises.
_RST_STREAM = (
    "<_MultiThreadedRendezvous of RPC that terminated with:\n"
    "\tstatus = StatusCode.INTERNAL\n"
    '\tdetails = "Received RST_STREAM with error code 2"\n'
    '\tdebug_error_string = "UNKNOWN:Error received from peer ipv4:192.0.2.1:443 '
    '{created_time:"2026-10-30T12:00:00.401+00:00", grpc_status:13, '
    'grpc_message:"Received RST_STREAM with error code 2"}"\n>'
)
_UNAVAILABLE = 'status = StatusCode.UNAVAILABLE\n\tdetails = "Socket closed"'


def test_overloaded_timeout_and_server_errors_retry():
    assert provider_failure_is_retryable("Error code: 529 - overloaded_error")
    assert provider_failure_is_retryable("Error code: 503 - The model is overloaded")
    assert provider_failure_is_retryable(
        "Error code: 500 - The server had an error processing your request"
    )
    assert provider_failure_is_retryable(
        "aknochow.claude.agent timed out after 900 seconds waiting on the Claude Agent SDK/CLI subprocess"
    )
    assert provider_failure_is_retryable("Error code: 429 - rate_limit_error")


def test_auth_permission_and_invalid_request_do_not_retry():
    assert not provider_failure_is_retryable("Error code: 401 - authentication_error")
    assert not provider_failure_is_retryable("Error code: 403 - permission_denied")
    assert not provider_failure_is_retryable("Error code: 400 - invalid_request_error")
    assert not provider_failure_is_retryable("'api_key' is required when backend=api")
    assert not provider_failure_is_retryable(
        "'api_key' or 'auth_token' is required when provider=anthropic"
    )


def test_unknown_message_does_not_retry():
    assert not provider_failure_is_retryable("widget exploded")
    assert not provider_failure_is_retryable("")


def test_transient_grpc_forms_retry():
    for text in (
        _RST_STREAM,
        _UNAVAILABLE,
        'status = StatusCode.INTERNAL details = "Received RST_STREAM with error code 2"',
        'status = StatusCode.UNAVAILABLE details = "upstream connect timeout"',
    ):
        assert provider_failure_is_retryable(text), text
        assert provider_failure_class(text) == "transient", text


def test_grpc_debug_digits_are_not_a_status_code():
    assert ".401+" in _RST_STREAM
    assert provider_failure_class(_RST_STREAM) == "transient"


def test_permanent_grpc_codes_do_not_retry():
    for text in (
        'status = StatusCode.UNAUTHENTICATED details = "connection reset by auth proxy"',
        'status = StatusCode.PERMISSION_DENIED details = "team has no access"',
        'status = StatusCode.INVALID_ARGUMENT details = "RST_STREAM is not a valid model"',
        'status = StatusCode.RESOURCE_EXHAUSTED details = "monthly spend reached"',
    ):
        assert not provider_failure_is_retryable(text), text


def test_bare_grpc_words_do_not_widen_other_families():
    assert not provider_failure_is_retryable("INTERNAL")
    assert not provider_failure_is_retryable("model UNAVAILABLE for this account")
    assert not provider_failure_is_retryable("StatusCode.INTERNAL: authentication backend rejected the key")


def test_other_families_keep_their_class_and_result():
    assert provider_failure_class("Error code: 429 - rate_limit_error") == "quota"
    assert provider_failure_class("Error code: 429 - insufficient_quota") == "quota"
    assert provider_failure_class("aknochow.claude.agent timed out after 900 seconds") == "timeout"
    assert provider_failure_class("Error code: 529 - overloaded_error") == "transient"
    assert provider_failure_class("Error code: 401 - authentication_error") == "permanent"
    assert provider_failure_class("widget exploded") == "unknown"
    assert provider_failure_is_retryable("Error code: 429 - insufficient_quota")
    assert provider_failure_is_retryable("aknochow.claude.agent timed out after 900 seconds")
