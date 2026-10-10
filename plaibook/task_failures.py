# -*- coding: utf-8 -*-
"""Record failed Ansible tasks and write them at the end of the play.

The callback displays this report. Quiet ``plai review`` swallows callback
output, so the CLI reprints the file when it is non-empty. The file is
written only for a path this module allows, and only when something failed.

Redaction catches accidental leaks: tokens in URLs, passwords in
commands, and private keys in error messages. It does not defend
against deliberately disguised text.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import unicodedata
from typing import Mapping

LOG_PREFIX = "plaibook-" + "task-failures-"
LOG_SUFFIX = ".log"
ENV_LOG = "PLAIBOOK_TASK_FAILURES_LOG"
_MESSAGE_LIMIT = 800
_FIELD_LIMIT = 500
# Userinfo may contain extra colons (user:p:ass). Stop at @, slash, or space.
_USERINFO = re.compile(r"://[^@/\s]+@")
# Longer keys first so api_key wins over key. Short keys may follow an
# underscore (DB_KEY, APP_AUTH). A letter before the key still excludes
# "monkey". "_" is a word character, so \b does not see that break.
_LONG_CREDENTIAL_KEYS = (
    "access_token|private_token|client_secret|id_token|refresh_token|api_key|"
    "password|passwd|signature|credential|secret|bearer|token"
)
_SHORT_CREDENTIAL_KEYS = "sig|auth|key"
# apiKey / privateKey. The optional separator also covers api_key and api-key.
# This is not fed through the CLI underscore replacement: the class is already here.
_CAMEL_CREDENTIAL_KEYS = r"api[-_]?key|private[-_]?key"
_CREDENTIAL_KEYS = f"{_LONG_CREDENTIAL_KEYS}|{_CAMEL_CREDENTIAL_KEYS}|{_SHORT_CREDENTIAL_KEYS}"
_SHORT_KEY = rf"(?<![A-Za-z0-9])(?:{_SHORT_CREDENTIAL_KEYS})"
_ASSIGN_KEY = rf"(?:{_LONG_CREDENTIAL_KEYS}|{_CAMEL_CREDENTIAL_KEYS}|{_SHORT_KEY})"
# A quoted value may contain spaces. \S+ stops at the first one, so
# A quoted password containing a space used to leave the tail in the log. The quote
# alternatives are disjoint (backslash vs not) and linear.
_QUOTED_VALUE = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''
_FIELD_VALUE = rf"(?:{_QUOTED_VALUE}|\S+)"
# A suffix such as _ID still belongs to the credential name:
# AWS_SECRET_ACCESS_KEY_ID=... must not stop at KEY.
_KEY_SUFFIX = r"(?:[_-][A-Za-z0-9]+)*"
_TOKEN_QUERY = re.compile(
    rf"(?i)([?&#](?:{_CREDENTIAL_KEYS}){_KEY_SUFFIX}=)(?:{_QUOTED_VALUE}|[^&#\s]*)",
)
# ENV_STYLE names (GITHUB_TOKEN, DB_PASSWORD) match the key inside the name.
# A leading (?:[A-Za-z0-9]+_)* group made that search quadratic. An optional
# quote sits between a JSON/YAML key and its colon: "token": "...".
# A credential name with nothing after '=' or ':'. The value is the next line.
# Spaces and tabs only. A newline must not count, or ssh_key:\\n-----BEGIN
# treats the header as the value and the key body is left behind.
_HSPACE = r"[ \t]*"
_CREDENTIAL_VALUE_FOLLOWS = re.compile(
    rf"(?i)(?:{_ASSIGN_KEY}){_KEY_SUFFIX}{_HSPACE}[=:]{_HSPACE}$"
)
_CREDENTIAL_ASSIGN = re.compile(
    rf"(?i)((?:{_ASSIGN_KEY}){_KEY_SUFFIX}[\"']?)"
    rf"({_HSPACE}[=:]{_HSPACE}){_FIELD_VALUE}",
)
# A fully quoted header value ("Bearer alpha beta") has no separate scheme token.
_AUTH_QUOTED = re.compile(
    rf"(?i)(authorization\s*[:=]\s*)(?:{_QUOTED_VALUE})",
)
_AUTH_HEADER = re.compile(
    rf"(?i)(authorization\s*[:=]\s*(?:bearer|basic|token)\s+){_FIELD_VALUE}",
)
_BEARER = re.compile(
    rf"(?i)(\bbearer\s+)(?:{_QUOTED_VALUE}|[A-Za-z0-9._~+/=-]{{8,}})",
)
# -u "user:pass word" is one quoted argument. -u alice:"alpha beta" keeps
# the username outside the quotes, so the password quote has to be read
# after the colon. \S+ used to stop inside that quote.
# --user before -u so --user is not parsed as -u plus a username.
# Whitespace or '=' may be absent: curl -uuser:secret.
# The lookbehind keeps -u from matching inside a word (build-utils).
_DASH_USER_HEAD = r"(?<![A-Za-z0-9-])(?:--user|-u)(?:\s+|=)?"
_DASH_USER_QUOTED = re.compile(
    rf"(?i)({_DASH_USER_HEAD})(?:{_QUOTED_VALUE})",
)
_DASH_USER = re.compile(
    rf"(?i)({_DASH_USER_HEAD})([^\s:]*:\s*){_FIELD_VALUE}",
)
# mysql/mysqldump/mariadb -p, including options between the command and
# -p (mysql -u root -psecret). psql -p is a port. The 800-character cap
# runs first, so the gap between the command and -p stays cheap.
_P_COMMAND = r"(?:mysql|mysqldump|mariadb)\b[^\n|;&]*?\s-[pP]"
_SHORT_P_SEPARATED = re.compile(
    rf"(?i)({_P_COMMAND}\s+){_FIELD_VALUE}",
)
_SHORT_P_ATTACHED = re.compile(
    rf"(?i)({_P_COMMAND})(?:{_QUOTED_VALUE}|\S+)",
)
# --token SECRET and --password "alpha beta". Assignment form (--password=SECRET)
# is already covered. Underscores in key names are also hyphens on the CLI.
# No leading (?:[A-Za-z0-9]+[-_])* group: that search is quadratic, and the
# key already matches inside the flag.
_CLI_LONG = _LONG_CREDENTIAL_KEYS.replace("_", "[-_]")
_CLI_CREDENTIAL_OPT = re.compile(
    rf"(?i)(--(?:{_CLI_LONG}|{_CAMEL_CREDENTIAL_KEYS}|{_SHORT_KEY}){_KEY_SUFFIX})"
    rf"(\s+){_FIELD_VALUE}",
)
# A bare token or a PEM block has no assignment delimiter.
# ghs_/gho_/ghu_/ghr_ are GitHub installation, OAuth, user-to-server,
# and refresh tokens. They show up without an assignment or a query key.
_PREFIX_TOKEN = re.compile(
    r"ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|ghs_[A-Za-z0-9_]{8,}|"
    r"gho_[A-Za-z0-9_]{8,}|ghu_[A-Za-z0-9_]{8,}|ghr_[A-Za-z0-9_]{8,}|"
    r"glpat-[A-Za-z0-9_\-]{8,}|sk-proj-[A-Za-z0-9_\-]{8,}|sk-ant-[A-Za-z0-9_\-]{8,}|"
    r"sk_live_[A-Za-z0-9]{8,}|sk_test_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{16}"
)
# A credential value that opens a quote. Used only to see whether the
# display cap cut the quote off; the value itself is not consumed here.
_CREDENTIAL_QUOTE_OPEN = re.compile(
    rf"(?i)(?:(?:{_ASSIGN_KEY}){_KEY_SUFFIX}[\"']?{_HSPACE}[=:]{_HSPACE}"
    rf"|(?:--(?:{_CLI_LONG}|{_CAMEL_CREDENTIAL_KEYS}|{_SHORT_KEY}){_KEY_SUFFIX})\s+"
    rf"|{_DASH_USER_HEAD}"
    rf"|authorization\s*[:=]\s*(?:(?:bearer|basic|token)\s+)?"
    rf"|\bbearer\s+"
    rf"|{_P_COMMAND})(?P<quote>[\"'])"
)
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
# END may have been cut off by the display cap. Hide through END, or
# through the end of this text when the closer is not here.
_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)
# Longer sequences before the two-byte Fe pattern. ESC ] / P / X / ^ / _
# open OSC, DCS, and the rest; matching only those two bytes leaves the
# tail of the sequence in the line and splits a PEM header.
# ESC ( B is a character-set switch. [ is not in the two-byte class, so
# CSI (ESC [ 31 m) is not consumed as a two-byte sequence.
_ANSI = re.compile(
    r"(?:"
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x9b[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[PX^_].*?(?:\x1b\\|\x07)"
    r"|\x1b[ -/]+[0-~]"
    r"|\x1b[@-Z\\-_]"
    r")"
)
# C0 except TAB/LF, DEL, and C1. CR is removed so it cannot rewind the line.
_C0_C1 = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _strip_format_chars(text: str) -> str:
    """Drop Unicode Cf characters (zero-width space, direction overrides)."""
    return "".join(char for char in text if unicodedata.category(char) != "Cf")


def _redact_display_text(text: object) -> str:
    raw = text if isinstance(text, str) else ""
    raw = _strip_format_chars(raw)
    raw = _ANSI.sub("", raw)
    raw = _C0_C1.sub("", raw)
    # Before assignment patterns. ssh_key:\\n-----BEGIN must not have the
    # header consumed as the assignment value.
    raw = _PEM_BLOCK.sub("***", raw)
    raw = _USERINFO.sub("://***@", raw)
    raw = _TOKEN_QUERY.sub(r"\1***", raw)
    raw = _AUTH_QUOTED.sub(r"\1***", raw)
    raw = _AUTH_HEADER.sub(r"\1***", raw)
    raw = _BEARER.sub(r"\1***", raw)
    raw = _DASH_USER_QUOTED.sub(r"\1***", raw)
    raw = _DASH_USER.sub(r"\1\2***", raw)
    raw = _SHORT_P_SEPARATED.sub(r"\1***", raw)
    raw = _SHORT_P_ATTACHED.sub(r"\1***", raw)
    raw = _CLI_CREDENTIAL_OPT.sub(r"\1\2***", raw)
    raw = _CREDENTIAL_ASSIGN.sub(r"\1\2***", raw)
    return _PREFIX_TOKEN.sub("***", raw)


def _scan_quote(text: str, quote: str, escaped: bool) -> tuple[int | None, bool]:
    """Index of the unescaped closer, or None, and the escape state at the end."""
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == quote:
            return index, False
    return None, escaped


def _dangling_quote(text: str) -> tuple[str, bool]:
    """A quote that opens on this line and never closes."""
    for index, char in enumerate(text):
        if char not in "\"'":
            continue
        closer, escaped = _scan_quote(text[index + 1 :], char, False)
        if closer is None:
            return char, escaped
    return "", False


def _unclosed_credential_quote(text: str) -> tuple[str, bool]:
    """Quote character and trailing escape state when a credential quote never closes."""
    pos = 0
    while True:
        match = _CREDENTIAL_QUOTE_OPEN.search(text, pos)
        if not match:
            return "", False
        quote = match.group("quote")
        closer, escaped = _scan_quote(text[match.end() :], quote, False)
        if closer is None:
            return quote, escaped
        pos = match.end() + closer + 1


def _mask_open_quoted_value(clipped: str) -> str:
    """Mask a credential quote that the display cap cut before it closed.

    ``\\S+`` would keep only the first word and leave the rest of the
    secret in the returned text. The closer is not in this slice, so
    the remainder of the slice is the secret.
    """
    pos = 0
    while True:
        match = _CREDENTIAL_QUOTE_OPEN.search(clipped, pos)
        if not match:
            return clipped
        quote = match.group("quote")
        closer, _escaped = _scan_quote(clipped[match.end() :], quote, False)
        if closer is not None:
            pos = match.end() + closer + 1
            continue
        return clipped[: match.end() - 1] + "***"


def _redact_bounded(text: str, *, limit: int) -> str:
    """Redact at most ``limit`` characters.

    The credential patterns are applied after the cut. A long run of
    ``key_a_a_…`` is quadratic in the suffix group, so the cut has to
    happen first. A quoted secret that starts inside the cut and does
    not close is masked through the end of the slice before that.
    """
    clipped = len(text) > limit
    raw = text[:limit] if clipped else text
    raw = _mask_open_quoted_value(raw)
    raw = _redact_display_text(raw)
    if clipped or len(raw) > limit:
        raw = raw[: limit - 1] + "…"
    return raw


def sanitize_failure_field(text: object, *, limit: int = _FIELD_LIMIT) -> str:
    """One task name, path, or host. No extra lines, controls, or URL secrets."""
    raw = text if isinstance(text, str) else ""
    raw = _redact_bounded(raw, limit=limit).replace("\t", " ").replace("\n", " ")
    raw = " ".join(raw.split())
    if len(raw) > limit:
        raw = raw[: limit - 1] + "…"
    return raw


def redact_failure_text(text: object) -> str:
    """Redact secrets in display text without inventing a replacement message.

    Each line is capped and redacted the same way as a failure log, including
    bearer tokens, credential assignments, and known token prefixes. Empty
    input stays empty.
    """
    raw = text if isinstance(text, str) else ""
    return _redact_log_lines(raw)


def clean_failure_message(text: object, *, limit: int = _MESSAGE_LIMIT) -> str:
    """One failure message, with URL secrets removed and length capped.

    The cap is applied before the line pass. Line state on that prefix
    still keeps a private key or quoted value hidden when its name ends
    the previous line.
    """
    raw = text if isinstance(text, str) else ""
    clipped = len(raw) > limit
    if clipped:
        raw = raw[:limit]
    raw = _redact_log_lines(raw).strip()
    if clipped or len(raw) > limit:
        raw = raw[: limit - 1] + "…"
    return raw or "task failed"


def note_failure(
    failures: list[dict[str, str]],
    *,
    task: str,
    message: object,
    path: str = "",
    host: str = "",
    ignored: bool = False,
    kind: str = "failed",
) -> list[dict[str, str]]:
    """Append one failure. ``kind`` is ``failed``, ``ignored``, or ``unreachable``."""
    failures.append(
        {
            "task": sanitize_failure_field(task) or "unnamed task",
            "message": clean_failure_message(message),
            "path": sanitize_failure_field(path),
            "host": sanitize_failure_field(host),
            "ignored": "true" if ignored else "false",
            "kind": kind if kind in {"failed", "ignored", "unreachable"} else "failed",
        }
    )
    return failures


def render_task_failures(failures: list[Mapping[str, str]], *, log_path: str = "") -> str:
    """Human report. Empty when there is nothing to say."""
    if not failures:
        return ""
    lines = [f"Task failures ({len(failures)}):"]
    if log_path:
        lines.append(f"Log: {log_path}")
    for item in failures:
        kind = item.get("kind") or "failed"
        if item.get("ignored") == "true" and kind == "failed":
            kind = "ignored"
        where = sanitize_failure_field(item.get("path") or "")
        task = sanitize_failure_field(item.get("task") or "") or "unnamed task"
        label = f"{task} ({where})" if where else task
        host = sanitize_failure_field(item.get("host") or "")
        host_prefix = f"{host}: " if host else ""
        lines.append(f"- [{kind}] {host_prefix}{label}")
        message = clean_failure_message(item.get("message") or "")
        for message_line in message.splitlines() or ["task failed"]:
            lines.append(f"  {message_line}")
    return "\n".join(lines) + "\n"


def is_loop_aggregate(payload: object) -> bool:
    """True when this registered result is a loop wrapper, not one item."""
    if not isinstance(payload, dict):
        return False
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return False
    first = results[0]
    return isinstance(first, dict) and bool(first.get("_ansible_item_result"))


def failure_message_from_result(payload: object) -> str:
    """Prefer ``msg``. Do not copy stdout, stderr, or exception text."""
    if not isinstance(payload, dict):
        return "task failed"
    msg = payload.get("msg")
    if isinstance(msg, str) and msg.strip():
        return msg
    if isinstance(msg, list):
        parts = [part for part in msg if isinstance(part, str) and part.strip()]
        if parts:
            return "\n".join(parts)
    return "task failed"


def _allowed_roots() -> list[str]:
    roots: list[str] = []
    cache_tmp = os.path.join(os.path.expanduser("~"), ".cache", "ansible-plaibook", "tmp")
    for raw in (tempfile.gettempdir(), cache_tmp):
        try:
            real = os.path.realpath(raw)
        except OSError:
            continue
        if real not in roots:
            roots.append(real)
    return roots


def allowed_task_failures_path(path: str) -> str | None:
    """Return ``path`` when it is a private CLI-owned failure log."""
    raw = (path or "").strip()
    if not raw or "\x00" in raw or not os.path.isabs(raw):
        return None
    name = os.path.basename(raw)
    if not name.startswith(LOG_PREFIX) or not name.endswith(LOG_SUFFIX):
        return None
    if os.sep in name or (os.altsep and os.altsep in name):
        return None
    try:
        real_parent = os.path.realpath(os.path.dirname(raw))
        under_root = False
        for root in _allowed_roots():
            try:
                if os.path.commonpath([root, real_parent]) == root:
                    under_root = True
                    break
            except ValueError:
                continue
        if not under_root:
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
    if not os.path.basename(real_parent).startswith(LOG_PREFIX):
        return None
    return os.path.join(real_parent, name)


# Device and inode of the file create_task_failures_log made. A later
# open of the same path must be that file. The callback rewrites this
# inode; a replaced path is not read or deleted.
# The descriptor stays open so the kernel cannot recycle that inode for
# a new file created at the same path after unlink or rename.
_LOG_IDENTITY: dict[str, tuple[int, int]] = {}
_LOG_HELD_FD: dict[str, int] = {}


def _remember_log_identity(path: str, fd: int) -> None:
    key = allowed_task_failures_path(path)
    if not key:
        return
    info = os.fstat(fd)
    held = os.dup(fd)
    try:
        os.set_inheritable(held, False)
    except OSError:
        os.close(held)
        raise
    previous = _LOG_HELD_FD.pop(key, None)
    if previous is not None:
        os.close(previous)
    _LOG_HELD_FD[key] = held
    _LOG_IDENTITY[key] = (info.st_dev, info.st_ino)


def _release_log_identity(path: str) -> None:
    key = allowed_task_failures_path(path)
    if not key:
        return
    _LOG_IDENTITY.pop(key, None)
    held = _LOG_HELD_FD.pop(key, None)
    if held is not None:
        os.close(held)


def _opened_log_is_original(path: str, info: os.stat_result) -> bool:
    expected = _LOG_IDENTITY.get(path)
    if expected is None:
        return True
    return (info.st_dev, info.st_ino) == expected


def create_task_failures_log(*, directory: str | None = None) -> tuple[str, str]:
    """Private 0o700 directory and empty 0o600 log. Caller deletes both when unused.

    A failure after the directory exists removes that directory and any
    file created in it. The CLI catches the error and continues, so a
    leftover directory would otherwise accumulate.
    """
    parent = tempfile.mkdtemp(prefix=LOG_PREFIX, dir=directory)
    fd = -1
    path = ""
    try:
        os.chmod(parent, 0o700)
        fd, path = tempfile.mkstemp(prefix=LOG_PREFIX, suffix=LOG_SUFFIX, dir=parent)
        os.fchmod(fd, 0o600)
        _remember_log_identity(path, fd)
    except OSError:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = -1
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
            _release_log_identity(path)
        try:
            os.rmdir(parent)
        except OSError:
            pass
        raise
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
    return parent, path


def write_task_failures(path: str, text: str) -> bool:
    """Replace a CLI-owned log. False means no-op (unsafe path, empty, or I/O)."""
    if not text.strip() or not hasattr(os, "O_NOFOLLOW"):
        return False
    resolved = allowed_task_failures_path(path)
    if resolved is None:
        return False
    # Truncate only after the inode checks. O_TRUNC on open would wipe a
    # hard-linked file before st_nlink can reject it.
    flags = (
        os.O_WRONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = -1
    try:
        fd = os.open(resolved, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return False
        if info.st_nlink != 1:
            return False
        if info.st_uid != os.geteuid():
            return False
        if stat.S_IMODE(info.st_mode) & 0o077:
            return False
        os.ftruncate(fd, 0)
        view = memoryview(text.encode("utf-8"))
        while len(view) > 0:
            written = os.write(fd, view)
            if written <= 0:
                os.ftruncate(fd, 0)
                return False
            view = view[written:]
        return True
    except OSError:
        if fd >= 0:
            try:
                os.ftruncate(fd, 0)
            except OSError:
                pass
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


_READ_LIMIT = 128_000


def read_task_failures_log(path: str) -> str:
    """Log text, or empty when the file is missing, unsafe, or only whitespace.

    Open with ``O_NOFOLLOW`` and ``O_NONBLOCK`` and the same owner/mode
    checks as the writer. A FIFO would otherwise block in open before
    the regular-file check.
    The playbook process can see ``PLAIBOOK_TASK_FAILURES_LOG`` and replace
    that path before the CLI reads it. Following a symlink would print the
    target. A different inode at the same path is left unread. The callback
    rewrites the file this process created.
    """
    if not path or not hasattr(os, "O_NOFOLLOW"):
        return ""
    resolved = allowed_task_failures_path(path)
    if resolved is None:
        return ""
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = -1
    try:
        fd = os.open(resolved, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return ""
        if info.st_nlink != 1:
            return ""
        if info.st_uid != os.geteuid():
            return ""
        if stat.S_IMODE(info.st_mode) & 0o077:
            return ""
        if not _opened_log_is_original(resolved, info):
            return ""
        truncated = info.st_size > _READ_LIMIT
        chunks: list[bytes] = []
        remaining = _READ_LIMIT
        while remaining > 0:
            data = os.read(fd, min(65536, remaining))
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        # The playbook can overwrite this file after the callback writes it.
        # Sanitize again at read time so quiet mode does not print raw bytes.
        # One line at a time, capped, so a single long line cannot make the
        # suffix search scan the whole read.
        text = _redact_log_lines(text)
        if truncated:
            text = text.rstrip() + "\n… failure log truncated\n"
    except OSError:
        return ""
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
    return text if text.strip() else ""


def _iter_lf_lines(text: str):
    """Split only on LF. A bare CR must not become a new line or be written back."""
    parts = text.split("\n")
    last = len(parts) - 1
    for index, part in enumerate(parts):
        yield part, "\n" if index < last else ""


def _visible_log_line(body: str) -> str:
    """Drop ANSI, C0/C1, and Unicode format characters before markers."""
    return _strip_format_chars(_C0_C1.sub("", _ANSI.sub("", body)))


def _redact_log_lines(text: str) -> str:
    """Redact each LF-delimited line, after cutting it to the message cap.

    A PEM block is split across lines, so the BEGIN and END markers are
    not on one line. Controls are removed before that check: an ANSI
    sequence or a NUL inside the header must still start the block.
    After a BEGIN line, every line is redacted until the END line. A
    quoted credential that does not close on its line stays open across
    the following lines until the matching quote.
    A newline inside an escape sequence is disguised text. The split
    happens before those controls are removed, and that case is out of scope.
    """
    if not text:
        return ""
    in_pem = False
    open_quote = ""
    quote_escaped = False
    mask_next = False
    pieces: list[str] = []
    for body, ending in _iter_lf_lines(text):
        # ANSI and C0/C1 can sit inside the header. The marker check has
        # to see the same text the redactor will, or a split BEGIN never
        # starts the block and the following key lines stay in the log.
        visible = _visible_log_line(body)
        if mask_next:
            mask_next = False
            pieces.append("***" + ending)
            # The hidden line can itself open a key or a quote. Keep
            # hiding until that block ends.
            if _PEM_BEGIN.search(visible) and not _PEM_END.search(visible):
                in_pem = True
            elif not _PEM_BEGIN.search(visible):
                probe = visible[:_MESSAGE_LIMIT]
                open_quote, quote_escaped = _unclosed_credential_quote(probe)
                if not open_quote:
                    open_quote, quote_escaped = _dangling_quote(probe)
            continue
        if open_quote:
            closer, quote_escaped = _scan_quote(visible, open_quote, quote_escaped)
            if closer is None:
                pieces.append("***" + ending)
                continue
            open_quote = ""
            tail = visible[closer + 1 :]
            pieces.append("***" + _redact_bounded(tail, limit=_MESSAGE_LIMIT) + ending)
            continue
        if in_pem:
            if _PEM_END.search(visible):
                in_pem = False
            pieces.append("***" + ending)
            continue
        if _PEM_BEGIN.search(visible) and not _PEM_END.search(visible):
            in_pem = True
        probe = visible[:_MESSAGE_LIMIT]
        open_quote, quote_escaped = _unclosed_credential_quote(probe)
        if not open_quote and _CREDENTIAL_VALUE_FOLLOWS.search(probe):
            mask_next = True
        pieces.append(_redact_bounded(body, limit=_MESSAGE_LIMIT) + ending)
    return "".join(pieces)


def discard_task_failure_log(path: str) -> None:
    """Remove a failure log and its private directory after the CLI has read it.

    The file exists so quiet mode can reprint what the callback displayed.
    Once that reprint has happened, the directory is not a record we keep.
    A path that no longer names the created inode is left in place.
    """
    resolved = allowed_task_failures_path(path) if path else None
    if resolved is None:
        return
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open(resolved, flags)
    except OSError:
        return
    try:
        info = os.fstat(fd)
        if not _opened_log_is_original(resolved, info):
            return
    finally:
        os.close(fd)
    parent = os.path.dirname(resolved)
    try:
        os.unlink(resolved)
    except OSError:
        return
    _release_log_identity(resolved)
    try:
        if os.path.basename(parent).startswith(LOG_PREFIX):
            os.rmdir(parent)
    except OSError:
        pass


def discard_empty_task_failure_log(path: str) -> None:
    """Remove an unused log and its private directory."""
    if not path or read_task_failures_log(path):
        return
    discard_task_failure_log(path)
