# -*- coding: utf-8 -*-
"""Keep PR @mentions from looking like git credentials, without hiding secrets.

``credentials-in-git-url`` matches across newlines: any http(s) URL,
the next colon, then a later at-github / at-gitlab / at-bitbucket host.
A github.com PR URL plus a github-advanced-security review comment is
that shape and is not a credential.

Do not insert characters into every newline to break that match: a
credential split across source lines (adjacent string literals, a line
continuation) uses the same spanning, and must still reach the scanner.
Do not drop unified-diff deletion lines: a credential added then
deleted in the same PR stays in git history and must still force
SECRET-001, including a deleted source line that starts with ``--``
(that becomes ``---`` in the patch and is not a ``--- a/file`` header).
Strip whitespace/quote-prefixed @user mentions (github-advanced-security)
only. Do not strip an @ that begins a git hostname (github.com and the
other hosts in the credentials-in-git-url rule), even when a quote
precedes it: adjacent string literals are a real credential.
Same-line userinfo and split-across-lines userinfo both still match.
"""

from __future__ import annotations

import re
from typing import Any

# Newline is not a mention prefix: "password\\n@github.com" is a split
# credential, not a review comment. @github.com is a git host even when
# a quote precedes it (adjacent string literals); @github-user is not.
_HOST_MENTION_RE = re.compile(
    r"(^|[ \t`\"'(\[])@(github|gitlab|bitbucket|dev\.azure)\b(?!\.(?:com|org)\b)"
)


def neutralize_host_mentions(text: str) -> str:
    if not isinstance(text, str):
        return text
    return _HOST_MENTION_RE.sub(r"\1\2", text)


def prepare_guardian_scan_input(text: str) -> str:
    """Scan-input transforms that do not hide a split git userinfo secret."""
    return neutralize_host_mentions(text)


_SECRET_DETECTED_PREFIX = re.compile(r"(?i)^secret detected:\s*")
# Display-name slugs from get_secret_type_display() when details.secret_type
# is missing. Canonical ids are the secrets.toml rule ids.
_SECRET_TYPE_ALIASES = {
    "environment-variable": "env-variable",
    "exported-environment-variable": "exported-env-variable",
    "password-secret-assignment": "generic-password-assignment",
    "long-hex-secret": "very-long-hex-secret",
    "long-base64-secret": "very-long-base64-secret",
    "hex-secret": "hex-secret-with-context",
    "base64-secret": "base64-secret-with-context",
    "credentials-embedded-in-git-remote-url": "credentials-in-git-url",
}


def _secret_type_slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _SECRET_DETECTED_PREFIX.sub("", text)
    text = re.sub(r"\s*\(.*\)$", "", text).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return _SECRET_TYPE_ALIASES.get(slug, slug)


def guardian_secret_type(finding: dict) -> str:
    """ai-guardian SECRET-001 subtype (toml rule id), or empty."""
    details = finding.get("details")
    if isinstance(details, dict):
        raw = details.get("secret_type") or details.get("rule_id")
        slug = _secret_type_slug(raw)
        if slug:
            return slug
    return _secret_type_slug(finding.get("message"))


# A captured value that is a reference or a stock placeholder, not a literal.
_PLACEHOLDER_SECRET_RE = re.compile(
    r"(?i)^(?:"
    r"password|passwd|token|secret|redacted|changeme|xxx+|x{8,}|your[_-]?token|"
    r"<[^>]+>|"
    r"\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*|%[_A-Za-z0-9]+%|\{\{[^}]+\}\}|"
    r"lookup\s*\(\s*['\"]env['\"][^)]*\)|"
    r"os\.environ(?:\.[A-Za-z_]+|\[[^\]]+\])?|"
    r"environ\.get\([^)]*\)"
    r")$"
)
_BEARER_VALUE_RE = re.compile(r"(?i)\bbearer\s+(\S+)")
_ASSIGNED_VALUE_RE = re.compile(r"""[:=]\s*['\"]([^'\"]*)['\"]""")
_UNQUOTED_REF_RE = re.compile(
    r"(?i)[:=]\s*("
    r"\$\{[^}\s]+\}|\$[A-Za-z_][A-Za-z0-9_]*|"
    r"lookup\s*\([^)]*\)|"
    r"os\.environ(?:\.[A-Za-z_]+|\[[^\]]+\])?|"
    r"environ\.get\([^)]*\)"
    r")"
)
# Prefix-backed credentials still block when a generic subtype reported them.
_PREFIX_TOKEN_RE = re.compile(
    r"(ghp_[A-Za-z0-9]|github_pat_|glpat-|sk-ant-|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)"
)


def _captured_secret(text: str) -> str:
    bearer = _BEARER_VALUE_RE.search(text)
    if bearer:
        return bearer.group(1).strip().strip("'\"")
    assigned = _ASSIGNED_VALUE_RE.search(text)
    if assigned:
        return assigned.group(1).strip()
    unquoted = _UNQUOTED_REF_RE.search(text)
    if unquoted:
        return unquoted.group(1).strip()
    return text.strip().strip("'\"")


def finding_has_prefix_token(finding: dict) -> bool:
    """True when the finding text contains a known token prefix or a PEM key."""
    parts = [_finding_secret_text(finding), str(finding.get("message") or "")]
    return _PREFIX_TOKEN_RE.search("\n".join(parts)) is not None


def _finding_secret_text(finding: dict) -> str:
    details = finding.get("details")
    if isinstance(details, dict):
        for key in ("match", "secret", "value", "raw"):
            raw = details.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    snippet = finding.get("snippet")
    if isinstance(snippet, str) and snippet.strip():
        return snippet.strip()
    return ""


def secret_value_is_placeholder(finding: dict) -> bool:
    """True when the captured secret is a reference or a stock placeholder.

    No captured text is not a placeholder: a credential-shaped rule with
    no value still blocks.
    """
    text = _finding_secret_text(finding)
    if not text:
        return False
    return _PLACEHOLDER_SECRET_RE.match(_captured_secret(text)) is not None


def blocking_guardian_findings(
    findings: Any,
    rule_ids: Any,
    informational_secret_types: Any = None,
) -> list[dict]:
    """Findings whose rule_id is configured to force NEEDS_CHANGES.

    SECRET-001 is one engine-agnostic bucket. Subtype alone does not
    demote a finding. A hit is informational only when the captured
    value is a placeholder or a variable reference. Prefix-backed
    tokens and PEM keys block even when the value sits in an env
    assignment or a long blob. ``informational_secret_types`` is
    accepted for callers and is not an unconditional bypass.
    """
    ids = {str(item) for item in (rule_ids or []) if item}
    _ = informational_secret_types
    out: list[dict] = []
    if not isinstance(findings, list):
        return out
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("rule_id") or "") not in ids:
            continue
        if finding_has_prefix_token(finding):
            out.append(finding)
            continue
        if secret_value_is_placeholder(finding):
            continue
        out.append(finding)
    return out


class FilterModule:
    def filters(self):
        return {
            "neutralize_host_mentions": neutralize_host_mentions,
            "prepare_guardian_scan_input": prepare_guardian_scan_input,
            "guardian_secret_type": guardian_secret_type,
            "secret_value_is_placeholder": secret_value_is_placeholder,
            "finding_has_prefix_token": finding_has_prefix_token,
            "blocking_guardian_findings": blocking_guardian_findings,
        }
