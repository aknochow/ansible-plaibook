# -*- coding: utf-8 -*-
"""Unit tests for the verify provider failure allowlist."""

from classify_verify_provider_failure import provider_failure_is_retryable


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
