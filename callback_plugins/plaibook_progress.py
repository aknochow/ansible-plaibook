# -*- coding: utf-8 -*-
"""Write the current review stage for the plaibook CLI spinner.

Aggregate callback: does not replace the default stdout callback. No-ops
unless PLAIBOOK_PROGRESS_FILE is set. Mapping lives in plaibook.progress
so tests and this plugin stay in sync; ansible-playbook without the
plaibook package installed simply skips stage updates.
"""

from __future__ import annotations

import os
from pathlib import Path

from ansible.plugins.callback import CallbackBase

try:
    from plaibook.progress import (
        clone_url_from_facts,
        clone_url_from_task_args,
        format_stage_line,
        stage_for_task,
    )
except ImportError:  # pragma: no cover - AAP/EE path without the CLI package

    def stage_for_task(name: str) -> str | None:
        return None

    def format_stage_line(stage: str, *, clone_url: str | None = None) -> str:
        return stage

    def clone_url_from_facts(facts: object) -> str | None:
        return None

    def clone_url_from_task_args(args: object) -> str | None:
        return None

DOCUMENTATION = """
    name: plaibook_progress
    type: aggregate
    short_description: Write coarse review stages for the plaibook spinner.
    description:
      - When PLAIBOOK_PROGRESS_FILE is set, writes a one-line stage name
        each time the review moves to a new main stage. Checkout includes
        review_clone_url / the git module repo when that fact is set.
    requirements: []
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "plaibook_progress"
    CALLBACK_NEEDS_ENABLED = False

    def __init__(self):
        super().__init__()
        self._path = (os.environ.get("PLAIBOOK_PROGRESS_FILE") or "").strip()
        self._stage = ""
        self._clone_url = ""
        self._line = ""

    def v2_playbook_on_task_start(self, task, is_conditional):
        if not self._path:
            return
        name = ""
        args = {}
        if task is not None:
            name = getattr(task, "get_name", lambda: "")() or str(getattr(task, "name", "") or "")
            raw_args = getattr(task, "args", None)
            if isinstance(raw_args, dict):
                args = raw_args
        url = clone_url_from_task_args(args)
        if url:
            self._clone_url = url
        stage = stage_for_task(name)
        if stage:
            self._stage = stage
        if not self._stage:
            return
        self._write_line()

    def v2_runner_on_ok(self, result):
        if not self._path:
            return
        payload = getattr(result, "_result", None) or {}
        url = clone_url_from_facts(payload.get("ansible_facts"))
        if not url:
            invocation = payload.get("invocation") or {}
            module_args = invocation.get("module_args") or {}
            url = clone_url_from_task_args(module_args)
        if not url:
            return
        self._clone_url = url
        if self._stage == "checkout" or not self._stage:
            if not self._stage:
                self._stage = "checkout"
            self._write_line()

    def _write_line(self) -> None:
        line = format_stage_line(self._stage, clone_url=self._clone_url)
        if not line or line == self._line:
            return
        self._line = line
        try:
            Path(self._path).write_text(line + "\n", encoding="utf-8")
        except OSError:
            return
