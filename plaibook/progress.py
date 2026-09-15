# -*- coding: utf-8 -*-
"""Map ansible-playbook task names to the few review stages the spinner shows."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

# First match wins. Unmapped tasks leave the current stage unchanged.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cache", ("same-commit fast path", "same commit (fast")),
    (
        "sandbox",
        (
            "openshell sandbox",
            "create the sandbox",
            "copy to sandbox",
            "setup_sandbox",
        ),
    ),
    (
        "checkout",
        (
            "clone the target",
            "fetch pr/mr",
            "fetch the pr",
            "parse and validate the review target",
            "resolve the review target",
            "check ci preflight",
            "verify the cloned commit",
            "review each target",
            "review briefing",
            "repo clone url",
        ),
    ),
    ("scan", ("ai-guardian", "guardian scan", "scan the shared diff")),
    (
        "lenses",
        (
            "security and review lens",
            "dispatch lenses",
            "dispatch_lens",
            "security lens",
            "review lens",
        ),
    ),
    ("merge", ("merge findings", "time the merge and score")),
    (
        "explore",
        (
            "exploration stage",
            "look beyond the diff",
            "explore turn",
            "dispatch_explore",
        ),
    ),
    (
        "verify",
        (
            "verification stage",
            "independently verify",
            "verify finding",
            "continuity-audit",
            "continuity audit",
        ),
    ),
    (
        "persist",
        (
            "persistence stage",
            "render and persist",
            "write last_run",
            "write the last_run",
        ),
    ),
    (
        "cleanup",
        (
            "teardown",
            "stop the playbook-owned cursor-sdk-bridge",
            "reap",
        ),
    ),
    (
        "setup",
        (
            "validate the selected provider",
            "provider runtime",
            "provider_preflight",
            "cursor-sdk-bridge sidecar",
            "load operator xdg",
            "align local module execution",
        ),
    ),
)


def stage_for_task(name: str) -> str | None:
    """Return a coarse stage for this ansible task name, or None to keep the last one."""
    haystack = " ".join((name or "").lower().split())
    if not haystack:
        return None
    for stage, needles in _RULES:
        if any(needle in haystack for needle in needles):
            return stage
    return None


def sanitize_clone_url(url: str) -> str:
    """Drop userinfo, query, and fragment so tokens never reach the spinner."""
    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return text
    try:
        parts = urlsplit(text)
    except ValueError:
        return "checkout"
    host = parts.hostname or ""
    if not host:
        return parts.scheme + "://" + (parts.path or "")
    netloc = host
    if parts.port:
        netloc = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def format_stage_line(stage: str, *, clone_url: str | None = None) -> str:
    """Human spinner line. Checkout includes the git URL when the playbook has it."""
    url = sanitize_clone_url(clone_url or "")
    if stage == "checkout" and url:
        return f"{stage} ({url})"
    return stage


def clone_url_from_facts(facts: object) -> str | None:
    if not isinstance(facts, dict):
        return None
    url = facts.get("review_clone_url")
    if isinstance(url, str) and url.strip() and "{{" not in url:
        cleaned = sanitize_clone_url(url.strip())
        return cleaned or None
    return None


def clone_url_from_task_args(args: object) -> str | None:
    if not isinstance(args, dict):
        return None
    repo = args.get("repo")
    if isinstance(repo, str) and ("://" in repo or repo.startswith("git@")) and "{{" not in repo:
        cleaned = sanitize_clone_url(repo.strip())
        return cleaned or None
    return None
