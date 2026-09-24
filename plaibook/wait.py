# -*- coding: utf-8 -*-
"""TTY wait animation for quiet `plai review` (stderr only)."""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import TextIO

from plaibook.summary import sanitize_display_line

# Braille spinner, same family as many CLI waiters (including Cursor).
_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"
_CLEAR_LINE = "\r\033[2K"
_UP1 = "\033[1A"
_DIM = "\033[2m"
_RESET = "\033[0m"
# Own CSI only (glyph truecolor + dim). Used to measure painted width.
_ANSI_CSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


def spinner_enabled(stream: TextIO | None = None) -> bool:
    """Animate only on a real TTY unless PLAIBOOK_SPINNER=0."""
    if os.environ.get("PLAIBOOK_SPINNER", "1").strip() in ("0", "false", "no"):
        return False
    target = stream if stream is not None else sys.stderr
    return bool(getattr(target, "isatty", lambda: False)())


def _use_color() -> bool:
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return True


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}:{secs:02d}"
    return f"{secs}s"


def terminal_columns(stream: TextIO | None = None) -> int:
    """Visible columns for spinner paint. Never wrap: \\033[1A is one physical row."""
    fileno = getattr(stream, "fileno", None)
    if callable(fileno):
        try:
            columns = os.get_terminal_size(fileno()).columns
            if columns > 0:
                return max(20, columns)
        except (OSError, ValueError, AttributeError):
            pass
    try:
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    except OSError:
        columns = 80
    return max(20, columns)


def visible_width(text: str) -> int:
    return len(_ANSI_CSI_RE.sub("", text or ""))


def clip_plain(text: str, width: int) -> str:
    """Truncate a single-width-character string to *width* columns."""
    if width <= 0:
        return ""
    raw = text or ""
    if len(raw) <= width:
        return raw
    if width == 1:
        return "…"
    return raw[: width - 1] + "…"


def hsv_to_rgb(h: float, s: float = 1.0, v: float = 1.0) -> tuple[int, int, int]:
    """h in [0, 1); full-saturation RGB for the spinner glyph."""
    h = h % 1.0
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i = i % 6
    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q
    return int(r * 255), int(g * 255), int(b * 255)


# HSV hue of pure blue; the spinner starts here and walks the circle.
_HUE_START_BLUE = 2.0 / 3.0
_MAX_WAIT_TEXT = 200


def spinner_rgb(elapsed: float) -> tuple[int, int, int]:
    """Walk the hue circle about once every 3 seconds, starting on blue."""
    return hsv_to_rgb((_HUE_START_BLUE + elapsed * 0.33) % 1.0, 1.0, 1.0)


def _safe_wait_text(text: str) -> str:
    return sanitize_display_line(text)[:_MAX_WAIT_TEXT]


class WaitSpinner:
    """Rewrite two stderr lines: spinner + label + elapsed, then the current stage.

    Each line is clipped to the terminal width. A long GitLab URL that wraps
    makes \\033[1A land on the wrapped remainder, so frames stack and the
    trailing ``s`` of ``0s`` is eaten. That is the "sometimes" quirk: short
    targets fit, full ``https://gitlab.../merge_requests/N`` URLs often do not.
    """

    def __init__(
        self,
        label: str,
        stream: TextIO | None = None,
        progress_file: str | Path | None = None,
        detail: str = "setup",
    ) -> None:
        self.label = _safe_wait_text(label)
        self.stream = stream if stream is not None else sys.stderr
        self._enabled = spinner_enabled(self.stream)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._progress_file = Path(progress_file) if progress_file else None
        self._detail = _safe_wait_text(detail) or "setup"
        self._painted_two_lines = False

    def __enter__(self) -> WaitSpinner:
        if not self._enabled:
            self.stream.write(self.label + "\n")
            self.stream.flush()
            return self
        self._started = time.monotonic()
        self.stream.write(_HIDE_CURSOR)
        self.stream.flush()
        self._thread = threading.Thread(target=self._run, name="plaibook-spinner", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if not self._enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._painted_two_lines:
            self.stream.write(_CLEAR_LINE + "\n" + _CLEAR_LINE + _UP1 + _CLEAR_LINE)
        else:
            self.stream.write(_CLEAR_LINE)
        self.stream.write(_SHOW_CURSOR)
        self.stream.flush()

    def _read_detail(self) -> str:
        if self._progress_file is not None:
            try:
                text = self._progress_file.read_text(encoding="utf-8").strip()
                if text:
                    return _safe_wait_text(text.splitlines()[-1])
            except OSError:
                pass
        return self._detail

    def _run(self) -> None:
        color = _use_color()
        i = 0
        while not self._stop.wait(0.08):
            frame = _FRAMES[i % len(_FRAMES)]
            now = time.monotonic()
            elapsed = format_elapsed(now - self._started)
            detail = self._read_detail() or "setup"
            width = terminal_columns(self.stream) - 1
            # spinner + space + label + two spaces + elapsed
            label = clip_plain(self.label, max(1, width - (1 + 1 + 2 + len(elapsed))))
            stage = clip_plain(detail, max(1, width - 2))
            if color:
                r, g, b = spinner_rgb(now - self._started)
                glyph = f"\033[38;2;{r};{g};{b}m{frame}{_RESET}"
                line1 = f"{glyph} {label}  {_DIM}{elapsed}{_RESET}"
                line2 = f"  {_DIM}{stage}{_RESET}"
            else:
                line1 = f"{frame} {label}  {elapsed}"
                line2 = f"  {stage}"
            self.stream.write(_CLEAR_LINE + line1 + "\n" + _CLEAR_LINE + line2 + _UP1)
            self.stream.flush()
            self._painted_two_lines = True
            i += 1
