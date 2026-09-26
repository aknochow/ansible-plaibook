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

import logging
import re
from typing import Any

_LOG = logging.getLogger("ansible.plugins.filter.scan_input")

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
_KNOWN_SECRET_TYPES = set(_SECRET_TYPE_ALIASES.values()) | {
    "env-variable",
    "exported-env-variable",
    "generic-password-assignment",
    "hex-secret-with-context",
    "very-long-hex-secret",
    "base64-secret-with-context",
    "very-long-base64-secret",
    "credentials-in-git-url",
    "github-personal-token",
    "json-api-key",
    "json-token",
    "json-password",
    "json-secret",
    "yaml-password",
    "bearer-token",
    "api-key-header",
    "auth-token-header",
}


def _secret_type_slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _SECRET_DETECTED_PREFIX.sub("", text)
    text = re.sub(r"\s*\(.*\)$", "", text).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if slug in _SECRET_TYPE_ALIASES:
        return _SECRET_TYPE_ALIASES[slug]
    if slug in _KNOWN_SECRET_TYPES:
        return slug
    if slug:
        _LOG.warning("unrecognized ai-guardian secret type")
    return ""


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
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%[_A-Za-z0-9]+%|"
    r"\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}|"
    r"\{\{\s*lookup\s*\(\s*['\"]env['\"]\s*,\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\)\s*\}\}|"
    r"lookup\s*\(\s*['\"]env['\"]\s*,\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\)|"
    r"os\.environ(?:\.[A-Za-z_]+|\[['\"][A-Za-z_][A-Za-z0-9_]*['\"]\])?|"
    r"os\.environ\.get\(\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\)|"
    r"environ\.get\(\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\)"
    r")$"
)
_BEARER_VALUE_RE = re.compile(r"(?i)\bbearer\s+(\S+)")
# Prefix-backed credentials still block when a generic subtype reported them.
_PREFIX_TOKEN_RE = re.compile(
    r"(ghp_[A-Za-z0-9]|github_pat_|glpat-|sk-ant-|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)"
)


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


_TRAILING_STRUCT_RE = re.compile(r"^[\s,}\]]*$")


def _rest_is_trailing_syntax(rest: str) -> bool:
    """True when text after a quoted scalar is structure or a YAML comment."""
    text = rest.strip()
    if not text or text.startswith("#"):
        return True
    if text.startswith(","):
        return True
    return _TRAILING_STRUCT_RE.fullmatch(text) is not None


def _assigned_value(rhs: str) -> str:
    """Take one quoted scalar, allowing only JSON/YAML punctuation after it."""
    rhs = rhs.strip()
    if rhs[:1] in "'\"":
        quote = rhs[0]
        end = rhs.find(quote, 1)
        if end != -1:
            rest = rhs[end + 1 :]
            if _rest_is_trailing_syntax(rest):
                return rhs[1:end]
            return rhs
    return _strip_wrapping_quotes(rhs)


def _captured_secret(text: str) -> str:
    """Return the whole assigned value, not a reference prefix inside it."""
    stripped = text.strip()
    bearer = _BEARER_VALUE_RE.search(stripped)
    if bearer and not stripped[bearer.end():].strip():
        return bearer.group(1).strip().strip("'\"")
    assigned = re.search(r"[:=]\s*(.*)$", stripped)
    if assigned:
        return _assigned_value(assigned.group(1))
    return _strip_wrapping_quotes(stripped)


def _candidate_texts(finding: dict) -> list[str]:
    """Every non-empty representation. A short match must not hide a longer one."""
    texts: list[str] = []
    details = finding.get("details")
    if isinstance(details, dict):
        for key in ("match", "secret", "value", "raw"):
            raw = details.get(key)
            if isinstance(raw, str) and raw.strip():
                texts.append(raw.strip())
    snippet = finding.get("snippet")
    if isinstance(snippet, str) and snippet.strip():
        texts.append(snippet.strip())
    return texts


def finding_has_prefix_token(finding: dict) -> bool:
    """True when any representation contains a known token prefix or a PEM key."""
    parts = _candidate_texts(finding)
    parts.append(str(finding.get("message") or ""))
    return _PREFIX_TOKEN_RE.search("\n".join(parts)) is not None


def _finding_secret_text(finding: dict) -> str:
    texts = _candidate_texts(finding)
    return texts[0] if texts else ""


_VALUE_SCALAR_RE = re.compile(r"""[:=]\s*(['"])(.*?)\1""", re.DOTALL)
_UNQUOTED_VALUE_RE = re.compile(
    r"(?i)[:=]\s*("
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|"
    r"os\.environ\.get\(\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\)|"
    r"os\.environ(?:\.[A-Za-z_]+|\[['\"][A-Za-z_][A-Za-z0-9_]*['\"]\])|"
    r"environ\.get\(\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\)|"
    r"lookup\s*\(\s*['\"]env['\"]\s*,\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\)|"
    r"[^\s'\"#,{}]+"
    r")"
)


def _snippet_scalars_are_placeholder(text: str) -> bool | None:
    """Classify every assigned scalar, quoted or not.

    Returns True only when every assigned value is a variable reference.
    A literal, including a short one, or an unquoted value after a comma,
    keeps the finding blocking.
    """
    saw_reference = False
    saw_value = False
    for match in _VALUE_SCALAR_RE.finditer(text):
        value = match.group(2)
        if not _rest_is_trailing_syntax(text[match.end() :]):
            return False
        saw_value = True
        if _PLACEHOLDER_SECRET_RE.match(value):
            saw_reference = True
        else:
            return False
    bare = _VALUE_SCALAR_RE.sub(" ", text)
    for match in _UNQUOTED_VALUE_RE.finditer(bare):
        value = match.group(1)
        nxt = bare[match.end() : match.end() + 1]
        if nxt and not nxt.isspace():
            return False
        saw_value = True
        if _PLACEHOLDER_SECRET_RE.match(value):
            saw_reference = True
        else:
            return False
    if saw_reference and saw_value:
        return True
    return None


def secret_value_is_placeholder(finding: dict) -> bool:
    """True when the captured secret is a variable reference.

    No captured text is not a placeholder: a credential-shaped rule with
    no value still blocks. A later JSON or YAML field is inspected, not
    only the first colon in the snippet.
    """
    texts = _candidate_texts(finding)
    if not texts:
        return False

    def _one(text: str) -> bool:
        scalars = _snippet_scalars_are_placeholder(text)
        if scalars is not None:
            return scalars
        return _PLACEHOLDER_SECRET_RE.match(_captured_secret(text)) is not None

    return all(_one(text) for text in texts)


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
    noisy = {_secret_type_slug(item) for item in (informational_secret_types or []) if item}
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
        secret_type = guardian_secret_type(finding)
        if secret_type and secret_type in noisy and secret_value_is_placeholder(finding):
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
