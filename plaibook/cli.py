# -*- coding: utf-8 -*-
"""plaibook CLI (also installed as `plai`). Thin wrapper around review.yml."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from plaibook import __version__
from plaibook.config import (
    FAMILIES,
    openshell_available,
    resolve_family,
    running_inside_openshell,
)
from plaibook.playbook import (
    PlaybookNotFoundError,
    build_ansible_command,
    find_playbook_root,
    generate_run_id,
    last_run_path,
    run_ansible_playbook,
)
from plaibook.summary import dump_json, dump_yaml, enrich_last_run, format_pretty, load_json
from plaibook.wait import WaitSpinner, spinner_enabled

USAGE_EPILOG = """\
plai and plaibook are the same program. The pip/uv distribution name is plaibook
(not plai, which is taken on PyPI, and not ansible-plaibook).

v1 locates review.yml in an ansible-plaibook checkout (--root / PLAIBOOK_ROOT /
package parents / cwd). It shells out to ansible-playbook and reads
last_run.<run_id>.json. It does not rescore findings or scrape playbook stdout.
It does not yet run ansible-playbook aknochow.plaibook.review (that FQCN lands
when plaibook is a collection).

Default stdout is a readable review (target, verdict, 0-100 scores,
Critical/Major with file:line + why). Quiet TTY waits show a spinner on
stderr (PLAIBOOK_SPINNER=0 to disable), with a second line for the current
stage (setup, checkout, scan, lenses, merge, explore, verify, persist).
--json / --yaml emit the structured last_run + summary fields. -v passes
-v to ansible-playbook (task names). -vv / --debug passes -vv (task names
and module args) and skips the spinner. --full (or -v) adds the findings.md
report. A score line plus finding counts is not a review.
Same-commit cache hits print that they reused the prior review (why cost
is $0.00). -f / --force disables that fast path and re-runs the lenses.

First review with no operator config prompts for a provider and writes
~/.config/ansible-plaibook/vars.yml. Cursor defaults to gpt-5.6-luna / high.
PR/branch reviews skip OpenShell when this process is already inside
an OpenShell sandbox, or when the SDK is not importable from this
interpreter (--sandbox to require it, --no-sandbox to skip it).

AAP / execution-environment jobs keep calling ansible-playbook review.yml.

Examples:
  plai review org/repo#123
  plaibook review org/repo#123
  plai review --commit
  plai review --commit --repo /path/to/repo --sha abc1234
  plai review org/repo#123 --json
  plai review org/repo#123 --yaml
  plai review org/repo#123 -v
  plai review org/repo#123 -vv
  plai review org/repo#123 --debug
  plai review org/repo#123 --full
  plai review org/repo#123 -f
  plai review org/repo#123 --provider cursor
  plai review org/repo#123 --no-sandbox
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
        description="Wrap ansible-playbook review.yml and print a readable review.",
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
        action="count",
        default=0,
        help=(
            "Pass -v to ansible-playbook (task names). Repeat for more "
            "(-vv / --debug shows module args)."
        ),
    )
    review.add_argument(
        "--debug",
        action="store_true",
        help="Debug mode: ansible-playbook -vv (task names and args). No spinner.",
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
        "--full",
        action="store_true",
        help="Include the full findings.md report after the pretty review.",
    )
    review.add_argument(
        "-f",
        "--force",
        action="store_true",
        help=(
            "Force a full review even when this commit was already reviewed "
            "(sets review_same_commit_fast_path_enabled=false)."
        ),
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
        "--provider",
        dest="provider",
        metavar="FAMILY",
        choices=list(FAMILIES),
        help=(
            "Set agent_family and save it to ~/.config/ansible-plaibook/vars.yml. "
            "cursor saves gpt-5.6-luna / high when those keys are unset."
        ),
    )
    review.add_argument(
        "--sandbox",
        dest="use_sandbox",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Run PR/branch briefing in an OpenShell sandbox. Default is on for "
            "pr/branch when the SDK is importable and this process is not "
            "already inside OpenShell."
        ),
    )
    review.add_argument(
        "-e",
        "--extra-var",
        action="append",
        default=[],
        dest="cli_extra_vars",
        metavar="KEY=VALUE",
        help="Pass extra-vars to ansible-playbook (repeatable). true/false become booleans.",
    )
    review.add_argument(
        "--root",
        dest="playbook_root",
        help="Plaibook checkout containing review.yml (or set PLAIBOOK_ROOT).",
    )
    return parser


def _parse_extra_var(item: str) -> tuple[str, object]:
    key, sep, value = item.partition("=")
    key = key.strip()
    if not sep or not key:
        raise ValueError(f"extra-var {item!r} must be KEY=VALUE")
    lowered = value.lower()
    if lowered == "true":
        return key, True
    if lowered == "false":
        return key, False
    return key, value


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
    if getattr(args, "use_sandbox", None) is not None:
        extras["use_sandbox"] = bool(args.use_sandbox)
    if getattr(args, "force", False):
        extras["review_same_commit_fast_path_enabled"] = False
    for item in getattr(args, "cli_extra_vars", None) or []:
        key, value = _parse_extra_var(item)
        extras[key] = value
    return extras


def ansible_verbosity(args: argparse.Namespace) -> int:
    """How many -v flags to pass to ansible-playbook.

    ``--debug`` is at least 2 (ansible -vv). ``-v`` as a boolean (tests)
    counts as 1.
    """
    raw = getattr(args, "verbose", 0)
    if raw is True:
        level = 1
    elif raw is False or raw is None:
        level = 0
    else:
        level = int(raw)
    if getattr(args, "debug", False):
        level = max(level, 2)
    return level


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


def _apply_sandbox_fallback(args: argparse.Namespace, extras: dict) -> str | None:
    """Skip nested OpenShell when we are already inside one, or the SDK is missing."""
    if extras.get("review_type") == "commit" and "use_sandbox" not in extras:
        return None
    if extras.get("use_sandbox") is True and not openshell_available():
        return (
            "OpenShell SDK is not importable from "
            f"{sys.executable}. Install it in this interpreter "
            "(pip install 'openshell>=0.0.116,<0.0.120'), or pass --no-sandbox. "
            "A copy in another venv does not count."
        )
    if "use_sandbox" in extras:
        return None
    if extras.get("review_type") == "commit":
        return None
    if running_inside_openshell():
        extras["use_sandbox"] = False
        return None
    if openshell_available():
        return None
    extras["use_sandbox"] = False
    sys.stderr.write(
        "OpenShell SDK is not importable from this interpreter; "
        "reviewing without a sandbox. Pass --sandbox to require it.\n"
    )
    return None


def _emit_summary(document: dict, args: argparse.Namespace) -> None:
    if args.as_json:
        dump_json(document, sys.stdout)
        return
    if args.as_yaml:
        dump_yaml(document, sys.stdout)
        return
    sys.stdout.write(
        format_pretty(
            document,
            full=bool(getattr(args, "full", False) or ansible_verbosity(args)),
        )
    )


def _progress_line(args: argparse.Namespace) -> str:
    if args.commit:
        repo = args.repo_path or "."
        sha = args.commit_sha or "HEAD"
        return f"Reviewing commit {sha} in {repo}\n"
    if args.branch_target:
        return f"Reviewing branch {args.branch_target}\n"
    return f"Reviewing {args.target}\n"


def _eval03_positive_control(user_input: str) -> object:
    """Eval 03 arm D plant. Intentional arbitrary code execution. Do not merge.

    Synthetic, non-secret. luna-high must report ``eval(user_input)`` or
    the Cursor lens output path cannot carry a finding.
    """
    return eval(user_input)


def cmd_review(args: argparse.Namespace) -> int:
    error = _validate_review_args(args)
    if error:
        print(error, file=sys.stderr)
        return 2

    # EVAL 03 POSITIVE CONTROL (arm D) — do not merge.
    # Arbitrary code execution of unsanitized CLI input via eval().
    user_input = (
        getattr(args, "review_extra_notes", None)
        or getattr(args, "target", None)
        or ""
    )
    if user_input:
        _eval03_positive_control(user_input)

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
    try:
        extras = extra_vars_from_args(args, run_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    extras.setdefault("ansible_python_interpreter", sys.executable)
    try:
        resolve_family(
            cli_family=getattr(args, "provider", None),
            stdin=sys.stdin,
            stderr=sys.stderr,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sandbox_error = _apply_sandbox_fallback(args, extras)
    if sandbox_error:
        print(sandbox_error, file=sys.stderr)
        return 2
    extras.setdefault("ansible_python_interpreter", sys.executable)
    try:
        command = build_ansible_command(
            extra_vars=extras,
            playbook_root=root,
            verbosity=ansible_verbosity(args),
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    structured = args.as_json or args.as_yaml
    passthrough = ansible_verbosity(args) > 0
    if passthrough:
        sys.stderr.write(_progress_line(args))
        sys.stderr.flush()
        result = run_ansible_playbook(command, playbook_root=root, verbose=True)
    elif spinner_enabled(sys.stderr):
        progress = tempfile.NamedTemporaryFile(
            prefix="plaibook-progress-",
            suffix=".txt",
            delete=False,
        )
        try:
            progress.write(b"setup\n")
            progress.close()
            with WaitSpinner(
                _progress_line(args).rstrip("\n"),
                stream=sys.stderr,
                progress_file=progress.name,
            ):
                result = run_ansible_playbook(
                    command,
                    playbook_root=root,
                    verbose=False,
                    env={"PLAIBOOK_PROGRESS_FILE": progress.name},
                )
        finally:
            Path(progress.name).unlink(missing_ok=True)
    else:
        if not structured:
            sys.stderr.write(_progress_line(args))
            sys.stderr.flush()
        result = run_ansible_playbook(command, playbook_root=root, verbose=False)
    summary_file = last_run_path(run_id)
    if not summary_file.is_file():
        if not passthrough:
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
