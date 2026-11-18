# -*- coding: utf-8 -*-
"""Map ansible-playbook task names to the few review stages the spinner shows."""

from __future__ import annotations

import os
import stat
import tempfile
from urllib.parse import urlsplit, urlunsplit

from plaibook.summary import sanitize_display_line

MAX_CLONE_URL_DISPLAY = 200
PROGRESS_FILE_PREFIX = "plaibook-progress-"
PROGRESS_FILE_SUFFIX = ".txt"
_PROGRESS_MAX_BYTES = 512

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
            "reap",
        ),
    ),
    (
        "setup",
        (
            "validate the selected provider",
            "provider runtime",
            "provider_preflight",
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
    """Drop userinfo, query, fragment, and terminal controls before the spinner."""
    text = sanitize_display_line(url or "").split(" ", 1)[0]
    if not text:
        return ""
    if "://" not in text:
        if text.startswith("git@") and ":" in text:
            return text[:MAX_CLONE_URL_DISPLAY]
        return ""
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if not host:
        rendered = parts.scheme + "://" + (parts.path or "")
    else:
        netloc = host if port is None else f"{host}:{port}"
        rendered = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    return rendered[:MAX_CLONE_URL_DISPLAY]


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


def create_progress_file(*, directory: str | None = None) -> tuple[str, str]:
    """Private 0o700 dir + 0o600 file for the spinner. Caller deletes both."""
    progress_dir = tempfile.mkdtemp(prefix=PROGRESS_FILE_PREFIX, dir=directory)
    os.chmod(progress_dir, 0o700)
    fd, progress_path = tempfile.mkstemp(
        prefix=PROGRESS_FILE_PREFIX,
        suffix=PROGRESS_FILE_SUFFIX,
        dir=progress_dir,
    )
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, b"setup\n")
    finally:
        os.close(fd)
    return progress_dir, progress_path


def _progress_allowed_roots() -> list[str]:
    """Process tempdir (tests / AAP) and the CLI cache scratch from #52."""
    from plaibook.playbook import RUNTIME_TMP_DIRNAME, last_run_dir

    roots: list[str] = []
    for raw in (
        tempfile.gettempdir(),
        str(last_run_dir() / RUNTIME_TMP_DIRNAME),
    ):
        try:
            real = os.path.realpath(raw)
        except OSError:
            continue
        if real not in roots:
            roots.append(real)
    return roots


def _parent_under_progress_root(real_parent: str) -> bool:
    for root in _progress_allowed_roots():
        try:
            if os.path.commonpath([root, real_parent]) == root:
                return True
        except ValueError:
            continue
    return False


def allowed_progress_path(path: str) -> str | None:
    """Return the path if it is a CLI-owned spinner file; otherwise None."""
    raw = (path or "").strip()
    if not raw or "\x00" in raw or not os.path.isabs(raw):
        return None
    name = os.path.basename(raw)
    if not name.startswith(PROGRESS_FILE_PREFIX) or not name.endswith(PROGRESS_FILE_SUFFIX):
        return None
    if os.sep in name or (os.altsep and os.altsep in name):
        return None
    try:
        real_parent = os.path.realpath(os.path.dirname(raw))
        if not _parent_under_progress_root(real_parent):
            return None
        parent_stat = os.stat(real_parent)
    except (OSError, ValueError):
        return None
    if not stat.S_ISDIR(parent_stat.st_mode):
        return None
    if parent_stat.st_uid != os.geteuid():
        return None
    if stat.S_IMODE(parent_stat.st_mode) & 0o077:
        return None
    if not os.path.basename(real_parent).startswith(PROGRESS_FILE_PREFIX):
        return None
    return os.path.join(real_parent, name)


def write_progress_line(path: str, line: str) -> bool:
    """Replace a CLI-owned spinner file. False means no-op (unsafe path or I/O)."""
    if not hasattr(os, "O_NOFOLLOW"):
        return False
    resolved = allowed_progress_path(path)
    if resolved is None:
        return False
    text = (line or "").splitlines()[0][:_PROGRESS_MAX_BYTES]
    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0)
    fd = -1
    try:
        fd = os.open(resolved, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return False
        if info.st_uid != os.geteuid():
            return False
        if stat.S_IMODE(info.st_mode) & 0o077:
            return False
        os.write(fd, (text + "\n").encode("utf-8"))
        return True
    except OSError:
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass

