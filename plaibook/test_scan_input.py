# -*- coding: utf-8 -*-
"""@host mentions in review context must not trip credentials-in-git-url."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

# ai-guardian 1.15.0 secrets.toml rule id credentials-in-git-url.
_GIT_CREDENTIAL_URL = re.compile(
    r"https?://[^:]+:(?!(?:PASSWORD|TOKEN|YOUR_TOKEN|xxx+|X{8,}|\$\{?\w+\}?|%\w+%)@)"
    r"[^@]{8,}@(github|gitlab|bitbucket|dev\.azure)"
)


def _filter():
    path = Path(__file__).resolve().parents[1] / "filter_plugins" / "scan_input.py"
    spec = importlib.util.spec_from_file_location("scan_input", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_pr_url_plus_github_mention_is_not_a_git_credential():
    mod = _filter()
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "+print('ok')\n"
    )
    description = (
        "- **URL**: https://github.com/aknochow/ansible-plaibook/pull/64\n"
        "- **CI**: passing\n"
        "\n"
        "`pip install git+https://github.com/aknochow/ansible-plaibook`\n"
        "- CI: Ubuntu 3.10\n"
        "1. @github-advanced-security: scorecard\n"
    )
    scanned = diff + "\n--- PR/MR description ---\n" + description
    assert _GIT_CREDENTIAL_URL.search(scanned)
    cleaned = mod.neutralize_host_mentions(scanned)
    assert "github-advanced-security" in cleaned
    assert _GIT_CREDENTIAL_URL.search(cleaned) is None


def test_backtick_host_mention_is_not_a_git_credential():
    mod = _filter()
    text = (
        "any ``https://`` URL, the next colon, then a later ``@github`` mention.\n"
        "PR metadata (``https://github.com/org/repo/pull/1``) plus ``@github-advanced-security``.\n"
    )
    assert _GIT_CREDENTIAL_URL.search(text)
    assert _GIT_CREDENTIAL_URL.search(mod.prepare_guardian_scan_input(text)) is None


def test_deleted_git_userinfo_line_still_matches_credentials_rule():
    mod = _filter()
    password = "s" + "ecretvalue1"
    text = (
        'clone = "https://github.com/org/repo.git"\n'
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        f'-    token = "https://x-access-token:{password}@'
        + "github.com/org/repo.git\"\n"
        "+    token = None\n"
    )
    assert _GIT_CREDENTIAL_URL.search(text)
    assert _GIT_CREDENTIAL_URL.search(mod.prepare_guardian_scan_input(text))


def test_deleted_double_dash_line_still_matches_credentials_rule():
    """A deleted `--` source line is `---` in the patch, not a file header."""
    mod = _filter()
    password = "s" + "ecretvalue1"
    text = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        f'---    token = "https://x-access-token:{password}@'
        + "github.com/org/repo.git\"\n"
        "+    token = None\n"
    )
    assert _GIT_CREDENTIAL_URL.search(text)
    assert _GIT_CREDENTIAL_URL.search(mod.prepare_guardian_scan_input(text))


def test_same_line_git_userinfo_still_matches_credentials_rule():
    mod = _filter()
    password = "s" + "ecretvalue1"
    url = "https://user:" + password + "@" + "github.com/org/repo.git"
    assert _GIT_CREDENTIAL_URL.search(url)
    cleaned = mod.prepare_guardian_scan_input("clone " + url + "\nnext line\n")
    assert url in cleaned
    assert _GIT_CREDENTIAL_URL.search(cleaned)


def test_split_line_git_userinfo_still_matches_credentials_rule():
    mod = _filter()
    password = "s" + "ecretvalue1"
    text = "https://user:\n" + password + "@" + "github.com/org/repo.git\n"
    assert _GIT_CREDENTIAL_URL.search(text)
    cleaned = mod.prepare_guardian_scan_input(text)
    assert password in cleaned
    assert _GIT_CREDENTIAL_URL.search(cleaned)


def test_quote_adjacent_git_host_still_matches_credentials_rule():
    """A quote immediately before the git host is not a mention prefix."""
    mod = _filter()
    password = "s" + "ecretvalue1"
    text = '"https://user:' + password + '" "@' + "github.com/org/repo.git\"\n"
    assert _GIT_CREDENTIAL_URL.search(text)
    cleaned = mod.prepare_guardian_scan_input(text)
    assert "@" + "github.com" in cleaned
    assert _GIT_CREDENTIAL_URL.search(cleaned)


def test_added_split_git_userinfo_in_diff_still_matches():
    mod = _filter()
    password = "s" + "ecretvalue1"
    text = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        '+url = ("https://user:"\n'
        f'+       "{password}@' + "github.com/org/repo.git\")\n"
    )
    assert _GIT_CREDENTIAL_URL.search(text)
    assert _GIT_CREDENTIAL_URL.search(mod.prepare_guardian_scan_input(text))


def test_quote_prefixed_github_user_mention_is_not_a_git_credential():
    mod = _filter()
    text = (
        "- **URL**: https://github.com/aknochow/ansible-plaibook/pull/64\n"
        "- **CI**: passing\n"
        'note: "@github-advanced-security" left a comment\n'
    )
    assert _GIT_CREDENTIAL_URL.search(text)
    cleaned = mod.prepare_guardian_scan_input(text)
    assert "github-advanced-security" in cleaned
    assert _GIT_CREDENTIAL_URL.search(cleaned) is None


def test_blocking_guardian_findings_keeps_credentials_in_git_url():
    mod = _filter()
    noisy = [
        "env-variable",
        "exported-env-variable",
        "generic-password-assignment",
        "very-long-base64-secret",
        "base64-secret-with-context",
        "json-token",
    ]
    findings = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Credentials In Git Url",
            "details": {"secret_type": "credentials-in-git-url"},
            "file_path": "ansible.xxx-ai-guardian-input.txt",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: GitHub Personal Access Token",
            "details": {"secret_type": "github-personal-token"},
            "file_path": "config.py",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "file_path": "includes/test_image_vertex.sh",
            "snippet": "export VERTEX_SA_KEY=${VERTEX_SA_KEY}",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Password/Secret Assignment",
            "details": {"secret_type": "generic-password-assignment"},
            "snippet": 'credential = "${DB_PASSWORD}"',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Long Base64 Secret",
            "details": {"secret_type": "very-long-base64-secret"},
            "snippet": "payload=${CI_PAYLOAD}",
        },
        {"rule_id": "PROMPT-INJECTION-001", "message": "Prompt injection detected"},
    ]
    blocking = mod.blocking_guardian_findings(findings, ["SECRET-001"], noisy)
    assert [item["details"]["secret_type"] for item in blocking] == [
        "credentials-in-git-url",
        "github-personal-token",
    ]


def test_credential_shaped_literal_blocks_and_placeholder_does_not():
    mod = _filter()
    findings = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: JSON Token",
            "details": {"secret_type": "json-token"},
            "snippet": '{"token": "ya29.literal-secret-value"}',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: JSON Token",
            "details": {"secret_type": "json-token"},
            "snippet": '{"token": "${CI_JOB_TOKEN}"}',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Bearer Token",
            "details": {"secret_type": "bearer-token"},
            "snippet": "Authorization: Bearer ${TOKEN}",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: API Key Header",
            "details": {"secret_type": "api-key-header"},
            "snippet": 'x-api-key: "PASSWORD"',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": "export TOKEN=${TOKEN}",
        },
    ]
    blocking = mod.blocking_guardian_findings(
        findings,
        ["SECRET-001"],
        ["env-variable"],
    )
    assert [item["details"]["secret_type"] for item in blocking] == [
        "json-token",
        "json-token",
        "bearer-token",
        "api-key-header",
    ]
    assert blocking[0]["snippet"].endswith('literal-secret-value"}')


def test_unquoted_references_are_placeholders_and_prefixes_still_block():
    mod = _filter()
    findings = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: API Key Header",
            "details": {"secret_type": "env-variable"},
            "snippet": "x-api-key: ${API_KEY}",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": "token: os.environ['TOKEN']",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": "lookup('env', 'TOKEN')",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Password Assignment",
            "details": {"secret_type": "generic-password-assignment"},
            "snippet": 'credential = "changeme"',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": "export GITHUB_TOKEN=ghp_x",
        },
    ]
    blocking = mod.blocking_guardian_findings(
        findings,
        ["SECRET-001"],
        ["env-variable", "generic-password-assignment", "very-long-base64-secret"],
    )
    assert [item["details"]["secret_type"] for item in blocking] == [
        "generic-password-assignment",
        "env-variable",
    ]


def test_reference_suffix_blocks_and_jinja_lookup_does_not():
    mod = _filter()
    findings = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Password Assignment",
            "details": {"secret_type": "generic-password-assignment"},
            "snippet": "credential=${PASSWORD}-hardcoded",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Password Assignment",
            "details": {"secret_type": "generic-password-assignment"},
            "snippet": 'credential="${PASSWORD}"hardcoded',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": "credential=${TOKEN}_suffix",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": "credential=${TOKEN}#hardcoded",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": "credential=${TOKEN},hardcoded",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": 'credential=${TOKEN} + "-hardcoded"',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": 'credential = os.environ.get("TOKEN")',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": "credential: lookup('env', 'TOKEN')",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "details": {"secret_type": "env-variable"},
            "snippet": "credential: \"{{ lookup('env', 'TOKEN') }}\"",
        },
    ]
    blocking = mod.blocking_guardian_findings(
        findings,
        ["SECRET-001"],
        ["env-variable", "generic-password-assignment"],
    )
    assert [item["snippet"] for item in blocking] == [
        "credential=${PASSWORD}-hardcoded",
        'credential="${PASSWORD}"hardcoded',
        "credential=${TOKEN}_suffix",
        "credential=${TOKEN}#hardcoded",
        "credential=${TOKEN},hardcoded",
        'credential=${TOKEN} + "-hardcoded"',
    ]
    hidden = {
        "rule_id": "SECRET-001",
        "message": "Secret detected: Environment Variable",
        "details": {"secret_type": "env-variable", "match": "${TOKEN}", "raw": "${TOKEN}ghp_x"},
        "snippet": "${TOKEN}ghp_x",
    }
    assert mod.blocking_guardian_findings(
        [hidden],
        ["SECRET-001"],
        ["env-variable"],
    ) == [hidden]


def test_json_wrapped_reference_is_placeholder_and_unknown_type_warns():
    import logging

    mod = _filter()
    findings = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: JSON Token",
            "details": {"secret_type": "json-token"},
            "snippet": '{"token": "${CI_JOB_TOKEN}"}',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: JSON Token",
            "details": {"secret_type": "json-token"},
            "snippet": '{"token": "${CI_JOB_TOKEN}"}extra',
        },
    ]
    blocking = mod.blocking_guardian_findings(
        findings,
        ["SECRET-001"],
        ["json-token"],
    )
    assert [item["snippet"] for item in blocking] == ['{"token": "${CI_JOB_TOKEN}"}extra']
    more = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: JSON Token",
            "details": {"secret_type": "json-token"},
            "snippet": '{"token": "${CI_JOB_TOKEN}", "kind": "oauth"}',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: JSON Token",
            "details": {"secret_type": "json-token"},
            "snippet": 'token: "${CI_JOB_TOKEN}" # supplied by CI',
        },
    ]
    assert mod.blocking_guardian_findings(more, ["SECRET-001"], ["json-token"]) == []
    comment_only = [more[1]]
    assert mod.blocking_guardian_findings(comment_only, ["SECRET-001"], ["json-token"]) == []
    leading = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: JSON Token",
            "details": {"secret_type": "json-token"},
            "snippet": '{"kind":"oauth","token":"${CI_JOB_TOKEN}"}',
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: JSON Token",
            "details": {"secret_type": "json-token"},
            "snippet": 'kind: oauth\ntoken: "${CI_JOB_TOKEN}"',
        },
    ]
    assert mod.blocking_guardian_findings(leading, ["SECRET-001"], ["json-token"]) == []
    comma_suffix = {
        "rule_id": "SECRET-001",
        "message": "Secret detected: Environment Variable",
        "details": {"secret_type": "env-variable"},
        "snippet": 'credential="${TOKEN}",hardcoded',
    }
    assert mod.blocking_guardian_findings(
        [comma_suffix],
        ["SECRET-001"],
        ["env-variable"],
    ) == [comma_suffix]
    short_literal = {
        "rule_id": "SECRET-001",
        "message": "Secret detected: JSON Token",
        "details": {"secret_type": "json-token"},
        "snippet": '{"token": "${TOKEN}", "pin": "hunter2"}',
    }
    assert mod.blocking_guardian_findings(
        [short_literal],
        ["SECRET-001"],
        ["json-token"],
    ) == [short_literal]
    mixed = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: YAML Password",
            "details": {"secret_type": "yaml-password"},
            "snippet": '{token: "${TOKEN}", password: supersecret}',
        },
    ]
    assert mod.blocking_guardian_findings(mixed, ["SECRET-001"], ["yaml-password"]) == mixed
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    logger = logging.getLogger("ansible.plugins.filter.scan_input")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        assert mod.guardian_secret_type({"message": "Secret detected: Not A Real Rule"}) == ""
    finally:
        logger.removeHandler(handler)
    assert records == ["unrecognized ai-guardian secret type"]


def test_blocking_guardian_findings_uses_message_when_details_missing():
    mod = _filter()
    findings = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Environment Variable",
            "snippet": "export FOO=${FOO}",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Credentials In Git Url",
        },
    ]
    blocking = mod.blocking_guardian_findings(findings, ["SECRET-001"], ["env-variable"])
    assert [item["message"] for item in blocking] == ["Secret detected: Credentials In Git Url"]


def test_placeholder_userinfo_is_preserved():
    mod = _filter()
    # PASSWORD is an ai-guardian placeholder; host is concatenated so this
    # file is not SECRET-001 bait even if the lookahead is ignored.
    placeholder_url = "https://user:PASSWORD@" + "github.com/org/repo.git"
    cleaned = mod.prepare_guardian_scan_input(f"clone {placeholder_url}\n")
    assert placeholder_url in cleaned
    assert "@github.com" in cleaned


_GENERIC_PASSWORD_ASSIGNMENT = re.compile(
    r"(?i)(?:password|passwd|secret|secret_key|api_secret|db_password|db_passwd)\s*=\s*[\"'][^\"']{8,}"
)
_ENV_VARIABLE_ASSIGNMENT = re.compile(r"""([A-Z][A-Z0-9_]+)\s*=\s*(["']?)([A-Za-z0-9\-_+/=]{16,})\2""")
_ALL_CAPS_VALUE = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)+$")
_SECRET_SCAN_FILES = (
    "plaibook/cursor_http2_proxy.py",
    "plaibook/openshell_sdk.py",
    "plaibook/test_collections.py",
    "plaibook/test_cursor_http2_proxy.py",
    "plaibook/test_openshell_sdk.py",
    "plaibook/test_scan_input.py",
    "scripts/ci-install-collections.py",
    "scripts/test_ci_install_collections.py",
)


def test_python_sources_do_not_trip_generic_secret_assignment_rules():
    """ai-guardian maps these matches to SECRET-001; keep fixtures from matching.

    Scan the files themselves rather than ``git diff origin/main``: CI checkouts
    are shallow and often have no main ref, which skipped every Test job.
    """
    root = Path(__file__).resolve().parents[1]
    password_hits = []
    env_hits = []
    for rel in _SECRET_SCAN_FILES:
        text = (root / rel).read_text(encoding="utf-8")
        for match in _GENERIC_PASSWORD_ASSIGNMENT.finditer(text):
            password_hits.append(f"{rel}: {match.group(0)[:80]}")
        for match in _ENV_VARIABLE_ASSIGNMENT.finditer(text):
            value = match.group(3)
            if value.startswith("_") or _ALL_CAPS_VALUE.match(value):
                continue
            env_hits.append(f"{rel}: {match.group(0)[:80]}")
    assert password_hits == []
    assert env_hits == []
