#!/usr/bin/python
# Copyright: (c) 2026, Adam Knochowski (@aknochow)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline stand-in for aknochow.cursor.agent in playbook tests.

Reads CURSOR_AGENT_STUB_FILE (JSON object). If the object has failed=true,
fail_json with the remaining keys (the WaitLiveRun-after-drain shape).
Otherwise exit_json. A top-level ``responses`` list is a sequence: each
call consumes the first object, writes the remainder back, and increments
``calls``. Never calls cursor-sdk or the network.
"""

from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
---
module: cursor_agent_stub
short_description: Offline stand-in for aknochow.cursor.agent
description:
  - Test double used by tests/test_cursor_named_lens_retry.yml.
  - Returns the JSON object in E(CURSOR_AGENT_STUB_FILE).
  - Does not call cursor-sdk.
author:
  - Adam Knochowski (@aknochow)
options:
  prompt:
    description: Ignored. Matches aknochow.cursor.agent.
    type: str
    required: true
  model:
    description: Ignored. Matches aknochow.cursor.agent.
    type: str
    required: true
  effort:
    description: Ignored. Matches aknochow.cursor.agent.
    type: str
  tools:
    description: Ignored. Matches aknochow.cursor.agent.
    type: list
    elements: str
  disallowed_tools:
    description: Ignored. Matches aknochow.cursor.agent.
    type: list
    elements: str
  structured_tool:
    description: Ignored. Matches aknochow.cursor.agent.
    type: dict
  agents:
    description: Ignored. Matches aknochow.cursor.agent.
    type: dict
  setting_sources:
    description: Ignored. Matches aknochow.cursor.agent.
    type: list
    elements: str
  mode:
    description: Ignored. Matches aknochow.cursor.agent.
    type: str
  bridge_timeout:
    description: Ignored. Matches aknochow.cursor.agent.
    type: float
  api_key:
    description: Ignored. Matches aknochow.cursor.agent.
    type: str
  cwd:
    description: Ignored. Matches aknochow.cursor.agent.
    type: path
    required: true
"""


def _next_payload(path: str, payload: dict) -> dict:
    """Return this call's result. A responses list is consumed one object at a time."""
    responses = payload.get("responses")
    if responses is None:
        return payload
    if not isinstance(responses, list) or not responses or not isinstance(responses[0], dict):
        raise ValueError("cursor agent stub responses must be a non-empty list of objects")
    current = dict(responses[0])
    updated = dict(payload)
    updated["responses"] = responses[1:]
    updated["calls"] = int(payload.get("calls") or 0) + 1
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(updated, handle)
    os.replace(temporary, path)
    return current


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            prompt=dict(type="str", required=True),
            model=dict(type="str", required=True),
            effort=dict(type="str"),
            tools=dict(type="list", elements="str"),
            disallowed_tools=dict(type="list", elements="str"),
            structured_tool=dict(type="dict"),
            agents=dict(type="dict"),
            setting_sources=dict(type="list", elements="str", default=[]),
            mode=dict(type="str"),
            bridge_timeout=dict(type="float"),
            api_key=dict(type="str", no_log=True),
            cwd=dict(type="path", required=True),
        ),
        supports_check_mode=False,
    )

    path = os.environ.get("CURSOR_AGENT_STUB_FILE", "")
    if not path:
        module.fail_json(msg="CURSOR_AGENT_STUB_FILE is unset; refusing a live Cursor call")
        return
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        module.fail_json(msg=f"cursor agent stub failed to read {path}: {exc}")
        return
    if not isinstance(payload, dict):
        module.fail_json(msg="cursor agent stub payload must be a JSON object")
        return
    try:
        payload = _next_payload(path, payload)
    except ValueError as exc:
        module.fail_json(msg=str(exc))
        return

    failed = bool(payload.pop("failed", False))
    payload["stub"] = True
    if failed:
        msg = payload.pop("msg", "cursor agent stub failure")
        module.fail_json(msg=msg, **payload)
        return
    module.exit_json(changed=False, **payload)


if __name__ == "__main__":
    main()
