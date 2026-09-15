# -*- coding: utf-8 -*-
"""Locate the plaibook checkout and shell out to ansible-playbook."""

from __future__ import annotations

import os
import secrets
import shutil
import signal
import string
import subprocess
import sys
from pathlib import Path

PLAYBOOK_NAME = "review.yml"
ANSIBLE_CFG_NAME = "ansible.cfg"
ENV_ROOT = "PLAIBOOK_ROOT"
ENV_TIMEOUT = "PLAIBOOK_PLAYBOOK_TIMEOUT"
DEFAULT_PLAYBOOK_TIMEOUT_SECONDS = 3600
RUN_ID_CHARS = string.ascii_letters + string.digits
RUN_ID_LENGTH = 16
CACHE_DIRNAME = "ansible-plaibook"


class PlaybookNotFoundError(FileNotFoundError):
    """review.yml could not be located from this install."""


class PlaybookTimeoutError(TimeoutError):
    """ansible-playbook exceeded PLAIBOOK_PLAYBOOK_TIMEOUT."""

    def __init__(self, seconds: float, command: list[str]):
        self.seconds = seconds
        self.command = command
        super().__init__(
            f"ansible-playbook exceeded {seconds:.0f}s timeout. "
            f"Set {ENV_TIMEOUT} to raise the limit (seconds)."
        )


def generate_run_id() -> str:
    """Match the playbook's password-lookup run_id alphabet and length."""
    return "".join(secrets.choice(RUN_ID_CHARS) for _ in range(RUN_ID_LENGTH))


def last_run_path(run_id: str, home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".cache" / CACHE_DIRNAME / f"last_run.{run_id}.json"


def find_playbook_root(start: Path | None = None, env: dict[str, str] | None = None) -> Path:
    """Find the checkout that contains review.yml + ansible.cfg.

    v1 is an editable install from a plaibook checkout. Search order:
    PLAIBOOK_ROOT, then parents of this package, then parents of *start* (cwd).
    """
    environ = os.environ if env is None else env
    explicit = environ.get(ENV_ROOT, "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if _is_playbook_root(root):
            return root
        raise PlaybookNotFoundError(
            f"{ENV_ROOT}={root} does not contain {PLAYBOOK_NAME} and {ANSIBLE_CFG_NAME}"
        )

    candidates: list[Path] = []
    here = Path(__file__).resolve().parent
    candidates.extend(here.parents)
    origin = (start or Path.cwd()).resolve()
    candidates.append(origin)
    candidates.extend(origin.parents)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _is_playbook_root(candidate):
            return candidate

    raise PlaybookNotFoundError(
        "Could not find review.yml. v1 of the plaibook CLI needs an editable "
        "install from a checkout (`pip install -e .` / `uv sync`). Set "
        f"{ENV_ROOT} to the ansible-plaibook checkout, or run from inside it."
    )


def _is_playbook_root(path: Path) -> bool:
    return (path / PLAYBOOK_NAME).is_file() and (path / ANSIBLE_CFG_NAME).is_file()


def ansible_playbook_bin() -> str:
    sibling = Path(sys.executable).resolve().parent / "ansible-playbook"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    found = shutil.which("ansible-playbook")
    if found:
        return found
    raise FileNotFoundError(
        "ansible-playbook not found next to this interpreter or on PATH. "
        "Install from the plaibook checkout with `uv sync` / `pip install -e .`."
    )


def build_ansible_command(
    *,
    extra_vars: dict,
    playbook_root: Path,
    ansible_bin: str | None = None,
    verbosity: int = 0,
) -> list[str]:
    import json

    playbook = playbook_root / PLAYBOOK_NAME
    command = [ansible_bin or ansible_playbook_bin(), str(playbook)]
    if verbosity > 0:
        command.append("-" + ("v" * min(int(verbosity), 4)))
    command.extend(["-e", json.dumps(extra_vars, separators=(",", ":"))])
    return command


def playbook_timeout_seconds(env: dict[str, str] | None = None) -> float:
    """Seconds ansible-playbook may run before the CLI kills the process group."""
    environ = os.environ if env is None else env
    raw = (environ.get(ENV_TIMEOUT) or "").strip()
    if not raw:
        return float(DEFAULT_PLAYBOOK_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_TIMEOUT}={raw!r} must be a positive number of seconds") from exc
    if value <= 0:
        raise ValueError(f"{ENV_TIMEOUT}={raw!r} must be a positive number of seconds")
    return value


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def run_ansible_playbook(
    command: list[str],
    *,
    playbook_root: Path,
    verbose: bool,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ansible-playbook. Quiet mode captures output; -v inherits the TTY."""
    timeout = playbook_timeout_seconds()
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged["ANSIBLE_CONFIG"] = str(playbook_root / ANSIBLE_CFG_NAME)
    kwargs: dict = {
        "args": command,
        "env": merged,
        "text": True,
        "start_new_session": True,
    }
    if not verbose:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    proc = subprocess.Popen(**kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(proc)
        raise PlaybookTimeoutError(timeout, command) from exc
    return subprocess.CompletedProcess(command, proc.returncode, stdout or "", stderr or "")
