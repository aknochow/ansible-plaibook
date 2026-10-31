#!/usr/bin/python
# Copyright: (c) 2026, Adam Knochowski (@aknochow)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline stand-in for aknochow.xai.chat. Never calls a provider API.

XAI_CHAT_STUB_FILE holds a JSON object keyed by the conversation_id with its
run prefix removed: security, review, security-retry, review-retry, explore.
A value is one response, or a list of responses used in order. A response with
failed=true is a fail_json. Each call appends its key to <file>.calls. The
lenses run in parallel but under different keys, and an append is atomic.
"""

from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
---
module: xai_chat_stub
short_description: Offline stand-in for aknochow.xai.chat
description:
  - Test double used by tests/test_xai_transport_failures.yml.
  - Returns the response for this conversation_id from E(XAI_CHAT_STUB_FILE).
author:
  - Adam Knochowski (@aknochow)
options:
  conversation_id:
    description: Selects the response, after its run prefix is removed.
    type: str
    required: true
  model:
    description: Ignored.
    type: raw
  reasoning_effort:
    description: Ignored.
    type: raw
  max_tokens:
    description: Ignored.
    type: raw
  timeout:
    description: Ignored.
    type: raw
  messages:
    description: Ignored.
    type: raw
  response_schema:
    description: Ignored.
    type: raw
  tools:
    description: Ignored.
    type: raw
  tool_choice:
    description: Ignored.
    type: raw
"""


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            conversation_id=dict(type="str", required=True),
            model=dict(type="raw"),
            reasoning_effort=dict(type="raw"),
            max_tokens=dict(type="raw"),
            timeout=dict(type="raw"),
            messages=dict(type="raw"),
            response_schema=dict(type="raw"),
            tools=dict(type="raw"),
            tool_choice=dict(type="raw"),
        ),
        supports_check_mode=False,
    )
    path = os.environ.get("XAI_CHAT_STUB_FILE", "")
    if not path:
        module.fail_json(msg="XAI_CHAT_STUB_FILE is unset; refusing a live provider call")
        return
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)

    key = module.params["conversation_id"].split("-", 1)[1]
    calls_path = f"{path}.calls"
    earlier = 0
    if os.path.exists(calls_path):
        with open(calls_path, encoding="utf-8") as handle:
            earlier = handle.read().split().count(key)
    with open(calls_path, "a", encoding="utf-8") as handle:
        handle.write(f"{key}\n")

    if key not in payload:
        module.fail_json(msg=f"xai chat stub has no response for {key}")
        return
    response = payload[key]
    if isinstance(response, list):
        response = response[min(earlier, len(response) - 1)]
    response = dict(response)
    if response.pop("failed", False):
        module.fail_json(msg=response.pop("msg", "xai chat stub failure"), **response)
        return
    module.exit_json(changed=False, **response)


if __name__ == "__main__":
    main()
