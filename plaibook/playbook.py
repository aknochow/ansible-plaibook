# -*- coding: utf-8 -*-
"""Locate the plaibook checkout and shell out to ansible-playbook."""

from __future__ import annotations

import os
import secrets
import shutil
import string
import subprocess
import sys
from pathlib import Path

PLAYBOOK_NAME = "review.yml"
ANSIBLE_CFG_NAME = "ansible.cfg"
ENV_ROOT = "PLAIBOOK_ROOT"
RUN_ID_CHARS = string.ascii_letters + string.digits
RUN_ID_LENGTH = 16
CACHE_DIRNAME = "ansible-plaibook"


class PlaybookNotFoundError(FileNotFoundError):
    """review.yml could not be located from this install."""


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
) -> list[str]:
    import json

    playbook = playbook_root / PLAYBOOK_NAME
    return [
        ansible_bin or ansible_playbook_bin(),
        str(playbook),
        "-e",
        json.dumps(extra_vars, separators=(",", ":")),
    ]


def run_ansible_playbook(
    command: list[str],
    *,
    playbook_root: Path,
    verbose: bool,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ansible-playbook. Quiet mode captures output; -v inherits the TTY."""
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged["ANSIBLE_CONFIG"] = str(playbook_root / ANSIBLE_CFG_NAME)
    if verbose:
        return subprocess.run(command, env=merged, check=False, text=True)
    return subprocess.run(
        command,
        env=merged,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
