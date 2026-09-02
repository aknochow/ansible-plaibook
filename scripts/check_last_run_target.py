#!/usr/bin/env python3
"""Verify review artifacts (~/.cache/ansible-plaibook/last_run.json or
persisted summary.json) actually describe the target you expect,
before trusting content for programmatic decisions.

Why this exists: `last_run.json` is a shared global path overwritten
by any concurrent review run. Persisted canonical `summary.json` paths
({org/repo}/{branch}/{date}-{sha}/summary.json) are also last-write-wins
when re-reviewed. Documentation alone ("read the run-scoped file") has
failed across multiple independent sessions. This script provides the
mechanical backstop: it verifies that the recorded target (and optional
commit/branch/run_id) matches what you requested, refusing to return
unrelated data.

Usage:
    python3 scripts/check_last_run_target.py "org/repo#123"
    python3 scripts/check_last_run_target.py "org/repo#123" --file /path/to/last_run.json
    python3 scripts/check_last_run_target.py "org/repo#123" --file /path/to/summary.json
    python3 scripts/check_last_run_target.py "org/repo#123" --commit abc1234 --branch main

Exit 0 and print the matching target's JSON entry if found.
Exit 1 with a clear stderr message (and no stdout output) otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_LAST_RUN_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "ansible-plaibook", "last_run.json"
)


def _check_entry_matches(
    entry: dict,
    top_data: dict,
    expected_target: str,
    expected_commit: str | None = None,
    expected_branch: str | None = None,
    expected_run_id: str | None = None,
) -> tuple[bool, str]:
    """Check if entry matches target and optional commit/branch/run_id."""
    target_val = entry.get("target") or top_data.get("target")
    if target_val != expected_target:
        return False, f"target mismatch: expected {expected_target!r}, found {target_val!r}"

    if expected_commit:
        commit_val = entry.get("commit") or top_data.get("commit")
        if not commit_val or not (
            commit_val == expected_commit
            or commit_val.startswith(expected_commit)
            or expected_commit.startswith(commit_val)
        ):
            return False, f"commit mismatch: expected {expected_commit!r}, found {commit_val!r}"

    if expected_branch:
        branch_val = entry.get("branch") or top_data.get("branch")
        if branch_val != expected_branch:
            return False, f"branch mismatch: expected {expected_branch!r}, found {branch_val!r}"

    if expected_run_id:
        run_id_val = entry.get("run_id") or top_data.get("run_id") or top_data.get("persist_run_id")
        if run_id_val != expected_run_id:
            return False, f"run_id mismatch: expected {expected_run_id!r}, found {run_id_val!r}"

    return True, ""


def check_last_run_target(
    expected_target: str,
    path: str,
    expected_commit: str | None = None,
    expected_branch: str | None = None,
    expected_run_id: str | None = None,
) -> tuple[bool, str, dict | None]:
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

    if not isinstance(data, dict):
        return False, f"ERROR: {path} top-level is not a JSON object", None

    # Handle summary.json (single target at top-level) or last_run.json (targets list)
    if "targets" in data and isinstance(data["targets"], list):
        targets = data.get("targets", [])
        for t in targets:
            if isinstance(t, dict):
                matches, _ = _check_entry_matches(
                    t,
                    data,
                    expected_target,
                    expected_commit=expected_commit,
                    expected_branch=expected_branch,
                    expected_run_id=expected_run_id,
                )
                if matches:
                    return True, "", t

        recorded = ", ".join(repr(t.get("target")) for t in targets if isinstance(t, dict)) or "(no targets recorded)"
    elif "target" in data:
        matches, mismatch_reason = _check_entry_matches(
            data,
            data,
            expected_target,
            expected_commit=expected_commit,
            expected_branch=expected_branch,
            expected_run_id=expected_run_id,
        )
        if matches:
            return True, "", data
        recorded = repr(data.get("target"))
    else:
        recorded = "(no target or targets field found)"

    criteria = [repr(expected_target)]
    if expected_commit:
        criteria.append(f"commit={expected_commit!r}")
    if expected_branch:
        criteria.append(f"branch={expected_branch!r}")
    if expected_run_id:
        criteria.append(f"run_id={expected_run_id!r}")
    criteria_str = " with " + ", ".join(criteria[1:]) if len(criteria) > 1 else ""

    message = (
        f"MISMATCH: you asked about {expected_target!r}{criteria_str}, but {path} "
        f"currently describes {recorded}. This is a shared last-write-wins path "
        "-- any concurrent review.yml invocation overwrites it with no warning. "
        "If you have this invocation's own run-scoped path from its output "
        "(e.g. RESULT_SUMMARY_RUN_SCOPED or summary.<run_id>.json), read that instead -- "
        "it is immune to this collision. Otherwise, re-run the review."
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
        help=f"Path to last_run.json or summary.json (default: {DEFAULT_LAST_RUN_PATH})",
    )
    parser.add_argument("--commit", default=None, help="Optional expected commit SHA to verify")
    parser.add_argument("--branch", default=None, help="Optional expected branch name to verify")
    parser.add_argument("--run-id", default=None, help="Optional expected run ID to verify")
    args = parser.parse_args(argv)

    ok, message, match = check_last_run_target(
        args.expected_target,
        args.file,
        expected_commit=args.commit,
        expected_branch=args.branch,
        expected_run_id=args.run_id,
    )
    if not ok:
        print(message, file=sys.stderr)
        return 1

    print(json.dumps(match, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
