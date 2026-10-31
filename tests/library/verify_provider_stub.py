#!/usr/bin/python
# Copyright: (c) 2026, Adam Knochowski (@aknochow)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline stand-in for a verify provider module.

Reads VERIFY_PROVIDER_STUB_FILE (JSON object). If the object has
failed=true, fail_json with the remaining keys. Otherwise exit_json.
A top-level calls counter is incremented on every invocation. Never
calls a provider API.
"""

from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
---
module: verify_provider_stub
short_description: Offline stand-in for a verify provider module
description:
  - Test double used by tests/test_verify_provider_failure.yml.
  - Returns the JSON object in E(VERIFY_PROVIDER_STUB_FILE).
  - Accepts the combined arguments of the Claude, Claude CLI, Gemini,
    and OpenAI verify module calls. Does not call a provider API.
author:
  - Adam Knochowski (@aknochow)
options:
  prompt:
    description: Ignored.
    type: raw
  system_prompt:
    description: Ignored.
    type: raw
  system:
    description: Ignored.
    type: raw
  system_instruction:
    description: Ignored.
    type: raw
  model:
    description: Ignored.
    type: raw
  provider:
    description: Ignored.
    type: raw
  backend:
    description: Ignored.
    type: raw
  region:
    description: Ignored.
    type: raw
  project_id:
    description: Ignored.
    type: raw
  location:
    description: Ignored.
    type: raw
  api_key:
    description: Ignored.
    type: str
  messages:
    description: Ignored.
    type: raw
  contents:
    description: Ignored.
    type: raw
  tools:
    description: Ignored.
    type: raw
  tool_choice:
    description: Ignored.
    type: raw
  tool_config:
    description: Ignored.
    type: raw
  output_format:
    description: Ignored.
    type: raw
  max_tokens:
    description: Ignored.
    type: raw
  max_output_tokens:
    description: Ignored.
    type: raw
  max_completion_tokens:
    description: Ignored.
    type: raw
  max_turns:
    description: Ignored.
    type: raw
  timeout:
    description: Ignored.
    type: raw
  effort:
    description: Ignored.
    type: raw
  thinking_level:
    description: Ignored.
    type: raw
  reasoning_effort:
    description: Ignored.
    type: raw
  setting_sources:
    description: Ignored.
    type: raw
  cwd:
    description: Ignored.
    type: raw
  base_url:
    description: Ignored.
    type: raw
  llama_server_mode:
    description: Ignored.
    type: raw
  conversation_id:
    description: Ignored.
    type: raw
"""


def _write(path: str, payload: dict) -> None:
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _take(path: str, payload: dict) -> dict:
    """Return this call's result and record that the stub was invoked."""
    calls = int(payload.get("calls") or 0) + 1
    responses = payload.get("responses")
    if responses is None:
        current = {key: value for key, value in payload.items() if key != "calls"}
        updated = dict(payload)
        updated["calls"] = calls
    else:
        if not isinstance(responses, list) or not responses or not isinstance(responses[0], dict):
            raise ValueError("verify provider stub responses must be a non-empty list of objects")
        current = dict(responses[0])
        updated = dict(payload)
        updated["responses"] = responses[1:]
        updated["calls"] = calls
    _write(path, updated)
    return current


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            prompt=dict(type="raw"),
            system_prompt=dict(type="raw"),
            system=dict(type="raw"),
            system_instruction=dict(type="raw"),
            model=dict(type="raw"),
            provider=dict(type="raw"),
            backend=dict(type="raw"),
            region=dict(type="raw"),
            project_id=dict(type="raw"),
            location=dict(type="raw"),
            api_key=dict(type="str", no_log=True),
            messages=dict(type="raw"),
            contents=dict(type="raw"),
            tools=dict(type="raw"),
            tool_choice=dict(type="raw"),
            tool_config=dict(type="raw"),
            output_format=dict(type="raw"),
            max_tokens=dict(type="raw"),
            max_output_tokens=dict(type="raw"),
            max_completion_tokens=dict(type="raw"),
            max_turns=dict(type="raw"),
            timeout=dict(type="raw"),
            effort=dict(type="raw"),
            thinking_level=dict(type="raw"),
            reasoning_effort=dict(type="raw"),
            setting_sources=dict(type="raw"),
            cwd=dict(type="raw"),
            base_url=dict(type="raw"),
            llama_server_mode=dict(type="raw"),
            conversation_id=dict(type="raw"),
        ),
        supports_check_mode=False,
    )

    path = os.environ.get("VERIFY_PROVIDER_STUB_FILE", "")
    if not path:
        module.fail_json(msg="VERIFY_PROVIDER_STUB_FILE is unset; refusing a live provider call")
        return
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        module.fail_json(msg=f"verify provider stub failed to read {path}: {exc}")
        return
    if not isinstance(payload, dict):
        module.fail_json(msg="verify provider stub payload must be a JSON object")
        return
    try:
        selected = _take(path, payload)
    except ValueError as exc:
        module.fail_json(msg=str(exc))
        return

    failed = bool(selected.pop("failed", False))
    selected.pop("responses", None)
    if failed:
        msg = selected.pop("msg", "verify provider stub failure")
        module.fail_json(msg=msg, **selected)
        return
    module.exit_json(changed=False, **selected)


if __name__ == "__main__":
    main()
