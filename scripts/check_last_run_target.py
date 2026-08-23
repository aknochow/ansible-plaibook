#!/usr/bin/env python3
"""Verify ~/.cache/ansible-plaibook/last_run.json actually describes the
target you think it does, before trusting its content for anything.

Why this exists: last_run.json is a SHARED, fixed path -- any
concurrent review.yml invocation (this repo or a different one, this
terminal or a different session) overwrites it, with no error or
mismatch warning. Confirmed live, more than once
(handoff.ansible-plaibook-shared-last-run-cache-collision.yaml,
handoff.ansible-plaibook-last-run-cache-collision-recurrence.yaml): a session
read this file expecting its own just-run target and silently got a
completely different one instead. SKILL.md already tells readers to
prefer the run-scoped last_run.<run_id>.json sibling (immune to this),
but across three independent real occurrences, documentation alone
hasn't been enough to prevent someone from reading the shared path
directly anyway. This script is the mechanical backstop: it loudly
refuses to hand back data for a target it doesn't actually contain,
instead of silently returning whatever happens to be there.

Usage:
    python3 scripts/check_last_run_target.py "org/repo#123"
    python3 scripts/check_last_run_target.py "org/repo#123" --file /path/to/last_run.json

Exit 0 and print the matching target's JSON entry if found.
Exit 1 with a clear stderr message (and no stdout output) otherwise --
whether that's because the file describes a different target entirely,
doesn't exist, or isn't valid JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


DEFAULT_LAST_RUN_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "ansible-plaibook", "last_run.json"
)


def check_last_run_target(expected_target: str, path: str) -> tuple[bool, str, dict | None]:
    """Return (ok, message, matching_target_entry_or_None)."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return False, f"ERROR: {path} does not exist -- has review.yml ever run?", None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"ERROR: {path} is not valid JSON: {exc}", None

    targets = data.get("targets", [])
    matches = [t for t in targets if t.get("target") == expected_target]
    if matches:
        return True, "", matches[0]

    recorded = ", ".join(repr(t.get("target")) for t in targets) or "(no targets recorded)"
    message = (
        f"MISMATCH: you asked about {expected_target!r}, but {path} "
        f"currently describes {recorded}. This is the SHARED last_run.json "
        "path -- any concurrent review.yml invocation (this repo or a "
        "different one) overwrites it with no warning. If you have this "
        "invocation's own RESULT_SUMMARY_RUN_SCOPED path from its stdout, "
        "read that instead -- it's immune to this collision. Otherwise, "
        "the result you're looking for may simply be gone; re-run the review."
    )
    return False, message, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("expected_target", help='The target you expect, e.g. "org/repo#123"')
    parser.add_argument(
        "--file",
        default=DEFAULT_LAST_RUN_PATH,
        help=f"Path to last_run.json (default: {DEFAULT_LAST_RUN_PATH})",
    )
    args = parser.parse_args(argv)

    ok, message, match = check_last_run_target(args.expected_target, args.file)
    if not ok:
        print(message, file=sys.stderr)
        return 1

    print(json.dumps(match, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
