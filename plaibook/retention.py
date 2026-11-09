# -*- coding: utf-8 -*-
"""Remove old run files from the plaibook cache.

``plai review`` calls :func:`prune_run_files` before each run. It removes,
when older than ``review_log_retention_days`` (default 14, 0 keeps
everything):

- ``last_run.<run_id>.json`` result files
- entries in the scratch directory (``tmp/``): task-failure logs, leftover
  run scratch, and clones from runs that were killed
- per-target prompt dump directories under ``debug_prompts/``; each run of
  a target rewrites its own, so the directory is pruned as a whole

An entry is old when it and everything inside it were last modified
before the cutoff. A review is capped by the
playbook timeout, far below a day, so nothing a running review holds is old
enough to match. Reports, the ledger, collections, and lock files are never
touched. Symlinks are never followed: one directly in a pruned directory
is kept, and one inside an old directory is unlinked with it.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from plaibook.playbook import RUN_ID_CHARS, RUN_ID_LENGTH, RUNTIME_TMP_DIRNAME, last_run_dir

SETTING = "review_log_retention_days"
DEFAULT_DAYS = 14
DEBUG_PROMPTS_DIRNAME = "debug_prompts"
_LAST_RUN = re.compile(rf"last_run\.[{re.escape(RUN_ID_CHARS)}]{{{RUN_ID_LENGTH}}}\.json")


@dataclass
class PruneResult:
    removed: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def retention_days(extras: Mapping[str, object], operator_vars: Mapping[str, object]) -> int:
    """Extra-vars, then vars.yml, then the default. Rejects a negative or non-integer value."""
    value = extras.get(SETTING, operator_vars.get(SETTING, DEFAULT_DAYS))
    if isinstance(value, bool):
        raise ValueError(f"{SETTING} must be a whole number of days, not {value!r}")
    try:
        days = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{SETTING} must be a whole number of days, not {value!r}") from None
    if days < 0:
        raise ValueError(f"{SETTING} must be 0 or more, not {days}")
    return days


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_dir(name: str | Path, dir_fd: int | None = None) -> int:
    """Open a directory without following a symlink in its last component."""
    return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)


def _entries(fd: int) -> list[os.DirEntry]:
    with os.scandir(fd) as it:
        return list(it)


def _is_old(dir_fd: int, name: str, cutoff: float) -> bool:
    """True when the entry and, for a directory, everything in it predate ``cutoff``.

    A file rewritten inside an old directory does not change the
    directory's own mtime, so a directory is old only if all of it is.
    """
    info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if info.st_mtime >= cutoff:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return True
    fd = _open_dir(name, dir_fd)
    try:
        return all(_is_old(fd, entry.name, cutoff) for entry in _entries(fd))
    finally:
        os.close(fd)


def _remove(dir_fd: int, name: str) -> None:
    """Delete ``name`` under ``dir_fd``. Symlinks inside are unlinked, never followed."""
    info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=dir_fd)
        return
    fd = _open_dir(name, dir_fd)
    try:
        for entry in _entries(fd):
            _remove(fd, entry.name)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=dir_fd)


def _prune_dir(
    fd: int,
    path: Path,
    cutoff: float,
    result: PruneResult,
    *,
    pattern: re.Pattern | None = None,
) -> None:
    try:
        entries = _entries(fd)
    except OSError as exc:
        result.errors.append(f"{path}: {exc}")
        return
    for entry in entries:
        if pattern is not None and not pattern.fullmatch(entry.name):
            continue
        try:
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode) or (pattern is not None and not stat.S_ISREG(mode)):
                continue
            if not _is_old(fd, entry.name, cutoff):
                continue
            _remove(fd, entry.name)
        except FileNotFoundError:
            continue
        except (OSError, RecursionError) as exc:
            # RecursionError: a tree nested deeper than Python's recursion
            # limit, which a cloned repository can contain. Skip it.
            result.errors.append(f"{path / entry.name}: {exc}")
            continue
        result.removed.append(path / entry.name)


def prune_run_files(days: int, home: Path | None = None, *, now: float | None = None) -> PruneResult:
    """Remove run files older than ``days``. ``days == 0`` removes nothing.

    Every directory is opened without following symlinks and every removal
    is relative to an open directory, so swapping a directory for a symlink
    mid-prune cannot redirect a deletion outside the cache.
    """
    result = PruneResult()
    if days <= 0:
        return result
    cache = last_run_dir(home)
    cutoff = (time.time() if now is None else now) - days * 86400
    try:
        root = _open_dir(cache)
    except (FileNotFoundError, NotADirectoryError):
        return result
    except OSError as exc:
        # ELOOP: the cache itself is a symlink, which plai leaves alone.
        if exc.errno != errno.ELOOP:
            result.errors.append(f"{cache}: {exc}")
        return result
    try:
        _prune_dir(root, cache, cutoff, result, pattern=_LAST_RUN)
        for name in (RUNTIME_TMP_DIRNAME, DEBUG_PROMPTS_DIRNAME):
            try:
                fd = _open_dir(name, root)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                if exc.errno != errno.ELOOP:
                    result.errors.append(f"{cache / name}: {exc}")
                continue
            try:
                _prune_dir(fd, cache / name, cutoff, result)
            finally:
                os.close(fd)
    finally:
        os.close(root)
    return result
