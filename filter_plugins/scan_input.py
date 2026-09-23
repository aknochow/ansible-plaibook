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


def blocking_guardian_findings(
    findings: Any,
    rule_ids: Any,
    informational_secret_types: Any = None,
) -> list[dict]:
    """Findings whose rule_id is configured to force NEEDS_CHANGES.

    SECRET-001 is one engine-agnostic bucket. Generic assignment / env /
    long-blob rules fire on ordinary CI scripts (Vertex SA key plumbing,
    ``export FOO=...``) and must not steal the verdict. credentials-in-git-url
    and prefix-backed tokens still block.
    """
    ids = {str(item) for item in (rule_ids or []) if item}
    noisy = {_secret_type_slug(item) for item in (informational_secret_types or []) if item}
    out: list[dict] = []
    if not isinstance(findings, list):
        return out
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("rule_id") or "") not in ids:
            continue
        secret_type = guardian_secret_type(finding)
        if secret_type and secret_type in noisy:
            continue
        out.append(finding)
    return out


class FilterModule:
    def filters(self):
        return {
            "neutralize_host_mentions": neutralize_host_mentions,
            "prepare_guardian_scan_input": prepare_guardian_scan_input,
            "guardian_secret_type": guardian_secret_type,
            "blocking_guardian_findings": blocking_guardian_findings,
        }
