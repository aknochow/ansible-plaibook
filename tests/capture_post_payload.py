#!/usr/bin/env python3
"""Call the real post helper with http_post mocked and write its payload.

Playbook tests use this so they exercise post_github without a forge
token and without a switch in the production poster.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch


def main() -> int:
    if len(sys.argv) != 6:
        print(
            "usage: capture_post_payload.py github <owner/repo> <number> <findings> <dest>",
            file=sys.stderr,
        )
        return 2
    platform, project, number, findings, dest = sys.argv[1:6]
    if platform != "github":
        print(f"unsupported platform: {platform}", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "roles" / "review" / "files" / "pr_review"))
    import post_review_comment

    captured: dict[str, object] = {}

    def fake(url: str, payload: dict, headers: dict | None = None, timeout: int = 60) -> tuple[int, str]:
        del url, headers, timeout
        captured["payload"] = payload
        return 201, "{}"

    with patch.object(post_review_comment, "http_post", fake):
        post_review_comment.post_github(project, number, findings)
    payload = captured.get("payload")
    if not isinstance(payload, dict):
        print("post_github did not build a payload", file=sys.stderr)
        return 1
    Path(dest).write_text(json.dumps(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
