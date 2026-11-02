#!/usr/bin/env python3
"""Move review.checkout or review.caller to a commit, in the workflow, docs, and tests.

Run review.checkout first and merge it. Then run review.caller against that merge.
This stays a person's step: a bot that edits workflow files needs the workflows
permission, and these two pins decide what code runs next to the review secrets.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ".github/workflows/plai-review-run.yml"
CALLER = ".github/workflows/plai-review.yml"
QUOTING = ("docs/dependency-map.md", "docs/github-review-check.md", "scripts/test_plai_review_workflow.py")
_FIND = {
    "checkout": (RUN, re.compile(r'ref: "([0-9a-f]{40})"')),
    "caller": (CALLER, re.compile(r"plai-review-run\.yml@([0-9a-f]{40})")),
}
_SHA = re.compile(r"[0-9a-f]{40}")


def current_pin(pin: str, root: Path = ROOT) -> str:
    path, pattern = _FIND[pin]
    found = set(pattern.findall((root / path).read_text(encoding="utf-8")))
    if len(found) != 1:
        raise ValueError(f"expected one {pin} pin in {path}, found {len(found)}")
    return found.pop()


def move_pin(pin: str, commit: str, root: Path = ROOT) -> list[str]:
    """Replace the current pin everywhere it is quoted. Returns the changed paths."""
    if not _SHA.fullmatch(commit):
        raise ValueError("the commit must be a full 40-character SHA")
    old = current_pin(pin, root)
    changed = []
    for name in (_FIND[pin][0], *QUOTING):
        path = root / name
        text = path.read_text(encoding="utf-8")
        if old in text:
            path.write_text(text.replace(old, commit), encoding="utf-8")
            changed.append(name)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pin", choices=sorted(_FIND))
    parser.add_argument("commit", help="full SHA of the merge commit on main")
    args = parser.parse_args(argv)
    try:
        for name in move_pin(args.pin, args.commit):
            print(name)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
