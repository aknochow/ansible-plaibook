# -*- coding: utf-8 -*-
"""plaibook CLI (also installed as `plai`). Thin wrapper around review.yml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from plaibook import __version__
from plaibook.playbook import (
    PlaybookNotFoundError,
    build_ansible_command,
    find_playbook_root,
    generate_run_id,
    last_run_path,
    run_ansible_playbook,
)
from plaibook.summary import dump_json, dump_yaml, enrich_last_run, format_pretty, load_json

USAGE_EPILOG = """\
plai and plaibook are the same program. The pip/uv distribution name is plaibook
(not plai, which is taken on PyPI, and not ansible-plaibook).

v1 wraps review.yml from a plaibook checkout (`uv sync` / `pip install -e .`).
It shells out to ansible-playbook and reads last_run.<run_id>.json. It does not
rescore findings or scrape playbook stdout for the verdict.

AAP / execution-environment jobs keep calling ansible-playbook review.yml directly.

Examples:
  plai review org/repo#123
  plaibook review org/repo#123
  plai review --commit
  plai review --commit --repo /path/to/repo --sha abc1234
  plai review org/repo#123 --json
  plai review org/repo#123 --yaml
  plai review org/repo#123 -v
"""


def _prog_name() -> str:
    name = Path(sys.argv[0]).name
    if not name or name.endswith(".py") or name == "-c":
        return "plaibook"
    return name


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    program = prog or _prog_name()
    parser = argparse.ArgumentParser(
        prog=program,
        description="plaibook CLI (also installed as plai).",
        epilog=USAGE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"plaibook {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    review = sub.add_parser(
        "review",
        help="Run review.yml (pr / commit / branch).",
        description="Wrap ansible-playbook review.yml and print the structured summary.",
    )
    review.add_argument(
        "target",
        nargs="?",
        help="PR/MR identifier: org/repo#N, org/repo!N, or a full GitHub/GitLab URL.",
    )
    mode = review.add_mutually_exclusive_group()
    mode.add_argument(
        "--commit",
        action="store_true",
        help="review_type=commit (fast local single-commit check).",
    )
    mode.add_argument(
        "--branch",
        metavar="TARGET",
        dest="branch_target",
        help="review_type=branch with this branch_review_target.",
    )
    review.add_argument(
        "--repo",
        dest="repo_path",
        help="Local repo path for --commit (default: current directory).",
    )
    review.add_argument(
        "--sha",
        dest="commit_sha",
        help="Commit SHA for --commit (default: HEAD).",
    )
    review.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Pass through full ansible-playbook output.",
    )
    fmt = review.add_mutually_exclusive_group()
    fmt.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Write the structured summary to stdout (no pretty block).",
    )
    fmt.add_argument(
        "--yaml",
        action="store_true",
        dest="as_yaml",
        help="Write the structured summary to stdout as YAML (no pretty block).",
    )
    review.add_argument(
        "--notes",
        dest="review_extra_notes",
        help="Trusted operator notes for this run (JSON extra-vars, colons are safe).",
    )
    review.add_argument(
        "--post",
        action="store_true",
        help="Set post_results=true (write back to the PR/MR).",
    )
    review.add_argument(
        "--fail-on-regressions",
        dest="fail_on_regressions",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override fail_on_regressions (commit defaults to true).",
    )
    review.add_argument(
        "--root",
        dest="playbook_root",
        help="Plaibook checkout containing review.yml (or set PLAIBOOK_ROOT).",
    )
    return parser


def extra_vars_from_args(args: argparse.Namespace, run_id: str) -> dict:
    extras: dict = {"last_run_id": run_id}
    if args.commit:
        extras["review_type"] = "commit"
        if args.repo_path:
            extras["repo_path"] = args.repo_path
        if args.commit_sha:
            extras["commit_sha"] = args.commit_sha
    elif args.branch_target:
        extras["review_type"] = "branch"
        extras["branch_review_target"] = args.branch_target
    else:
        extras["review_type"] = "pr"
        extras["review_targets_raw"] = args.target
    if args.review_extra_notes:
        extras["review_extra_notes"] = args.review_extra_notes
    if args.post:
        extras["post_results"] = True
    if args.fail_on_regressions is not None:
        extras["fail_on_regressions"] = bool(args.fail_on_regressions)
    return extras


def _validate_review_args(args: argparse.Namespace) -> str | None:
    if args.commit:
        if args.target:
            return "review --commit does not take a PR/MR target"
        return None
    if args.branch_target:
        if args.target:
            return "review --branch does not take a positional PR/MR target"
        return None
    if not args.target:
        return "specify a PR/MR target, or --commit, or --branch TARGET"
    return None


def _emit_summary(document: dict, args: argparse.Namespace) -> None:
    if args.as_json:
        dump_json(document, sys.stdout)
        return
    if args.as_yaml:
        dump_yaml(document, sys.stdout)
        return
    sys.stdout.write(format_pretty(document))


def _progress_line(args: argparse.Namespace) -> str:
    if args.commit:
        repo = args.repo_path or "."
        sha = args.commit_sha or "HEAD"
        return f"Reviewing commit {sha} in {repo}\n"
    if args.branch_target:
        return f"Reviewing branch {args.branch_target}\n"
    return f"Reviewing {args.target}\n"


def cmd_review(args: argparse.Namespace) -> int:
    error = _validate_review_args(args)
    if error:
        print(error, file=sys.stderr)
        return 2

    try:
        root = (
            Path(args.playbook_root).expanduser().resolve()
            if args.playbook_root
            else find_playbook_root()
        )
        if args.playbook_root and not ((root / "review.yml").is_file() and (root / "ansible.cfg").is_file()):
            print(
                f"--root {root} does not contain review.yml and ansible.cfg",
                file=sys.stderr,
            )
            return 2
    except PlaybookNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    run_id = generate_run_id()
    extras = extra_vars_from_args(args, run_id)
    try:
        command = build_ansible_command(extra_vars=extras, playbook_root=root)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    structured = args.as_json or args.as_yaml
    if not structured:
        sys.stderr.write(_progress_line(args))
        sys.stderr.flush()

    result = run_ansible_playbook(command, playbook_root=root, verbose=args.verbose)
    summary_file = last_run_path(run_id)
    if not summary_file.is_file():
        if not args.verbose:
            captured = (result.stderr or "") + (result.stdout or "")
            if captured.strip():
                sys.stderr.write(captured)
                if not captured.endswith("\n"):
                    sys.stderr.write("\n")
        print(
            f"ansible-playbook exited {result.returncode} without writing {summary_file}",
            file=sys.stderr,
        )
        return result.returncode if result.returncode else 2

    document = enrich_last_run(load_json(summary_file), last_run_file=summary_file)
    _emit_summary(document, args)
    return result.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command != "review":
        parser.print_help()
        return 2
    return cmd_review(args)


if __name__ == "__main__":
    sys.exit(main())
