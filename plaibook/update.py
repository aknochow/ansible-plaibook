# -*- coding: utf-8 -*-
"""Upgrade an installed plaibook with pipx."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Callable

Run = Callable[..., subprocess.CompletedProcess[str]]


def upgrade_plaibook(*, run: Run = subprocess.run) -> int:
    """Run ``pipx upgrade plaibook``. Return the pipx exit code.

    pipx is required. A checkout install (``uv``, ``pip install -e``) is
    not upgraded here.
    """
    pipx = shutil.which("pipx")
    if not pipx:
        print(
            "pipx is not on PATH. Install plaibook with pipx, then run plai update.",
            file=sys.stderr,
        )
        return 1
    completed = run([pipx, "upgrade", "plaibook"], check=False)
    return int(completed.returncode)
