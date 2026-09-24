# -*- coding: utf-8 -*-
"""plaibook CLI (also installed as `plai`). Thin wrapper around review.yml."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from plaibook import __version__
from plaibook.collections import CollectionInstallError, ensure_collections
from plaibook.config import (
    FAMILIES,
    ConfigError,
    load_vars,
    openshell_available,
    resolve_family,
    running_inside_openshell,
)
from plaibook.openshell_sdk import OpenshellSdkError, reexec_sandbox_runtime
from plaibook.playbook import (
    PlaybookNotFoundError,
    PlaybookTimeoutError,
    ScratchDirError,
    build_ansible_command,
    find_playbook_root,
    generate_run_id,
    last_run_path,
    run_ansible_playbook,
    runtime_tmp_dir,
)
from plaibook.progress import create_progress_file
from plaibook.provider_sdk import ProviderSdkError, ensure_provider_sdk
from plaibook.summary import (
    SummaryError,
    dump_json,
    dump_yaml,
    enrich_last_run,
    format_pretty,
    load_json,
    sanitize_display_line,
)
from plaibook.update import (
    NetworkError,
    UpdateError,
    VersionError,
    current_version,
    download_pypi_wheel,
    fetch_pypi_latest_version,
    pip_install_from_path,
    pip_install_git_ref,
    prompt_confirm,
    update_cache_dir,
    verify_installation,
)
from plaibook.wait import WaitSpinner, spinner_enabled

USAGE_EPILOG = """\
plai and plaibook are the same program. The pip/uv distribution name is plaibook
(not plai, which is taken on PyPI, and not ansible-plaibook).

pip install plaibook vendors review.yml into the wheel. plai review with no
arguments reviews HEAD in the current directory. First run installs Galaxy
collections into ~/.cache/ansible-plaibook/collections (never ~/.ansible).
That install is the playbook; --root points at a local checkout instead.
The CLI shells out
to ansible-playbook and reads last_run.<run_id>.json. It does not rescore
findings or scrape playbook stdout. It does not yet run ansible-playbook
aknochow.plaibook.review (that FQCN lands when plaibook is a collection).

Default stdout is a readable review (target, verdict, 0-100 scores,
Critical/Major with file:line + why). Quiet TTY waits show a spinner on
stderr (PLAIBOOK_SPINNER=0 to disable), with a second line for the current
stage (setup, checkout, scan, lenses, merge, explore, verify, persist).
--json / --yaml emit the structured last_run + summary fields. -v passes
-v to ansible-playbook (task names). Combined with --json/--yaml, ansible
output goes to stderr so stdout stays parseable. -vv / --debug passes -vv
(task names and module args) and skips the spinner. --full (or -v) adds
the findings.md report. A score line plus finding counts is not a review.
Same-commit cache hits print that they reused the prior review (why cost
is $0.00). -f / --force disables that fast path and re-runs the lenses.
A SKIPPED verdict (CI failing on the PR head) prints why and the failing
check names; pass `-e review_require_ci_passing=false` to review anyway.

First review with no operator config prompts for a provider and writes
~/.config/ansible-plaibook/vars.yml. Cursor defaults to gpt-5.6-luna / high.
PR/branch reviews skip nested OpenShell when this process is already
inside an OpenShell sandbox. They fail closed if the SDK is not
importable from this interpreter (--no-sandbox to review on the host,
--sandbox to require it).

AAP / execution-environment jobs keep calling ansible-playbook review.yml.

Examples:
  pip install plaibook
  plai review
  plai review org/repo/123
  plai review org/repo/pull/123
  plaibook review org/repo/123
  plai review --commit
  plai review --commit --repo /path/to/repo --sha abc1234
  plai review org/repo/123 --json
  plai review org/repo/123 --yaml
  plai review org/repo/123 -v
  plai review org/repo/123 -vv
  plai review org/repo/123 --debug
  plai review org/repo/123 --full
  plai review org/repo/123 -f
  plai review org/repo/123 --provider cursor
  plai review org/repo/123 --no-sandbox
  plai update
  plai update --check
  plai update --branch main
  plai update --branch v0.1.26
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
        help="PR/MR identifier: org/repo/N, org/repo/pull/N, gitlab:org/repo/N, or a full GitHub/GitLab URL.",
    )
    mode = review.add_mutually_exclusive_group()
    mode.add_argument(
        "--commit",
        action="store_true",
        help=("review_type=commit. Default when no PR/MR target is given (reviews HEAD in the current directory)."),
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
        help=("Pass -v to ansible-playbook (task names). Repeat for more (-vv / --debug shows module args)."),
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
            "pr/branch when this process is not already inside OpenShell. "
            "Python 3.10 switches to ~/.cache/ansible-plaibook/sandbox-runtime "
            "(Python 3.11+). --no-sandbox stays on this interpreter."
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
        help="Use a local playbook checkout instead of this pip install.",
    )

    update = sub.add_parser(
        "update",
        help="Update plaibook to the latest version (or specific branch).",
        description="Reinstall plaibook from PyPI or a GitHub branch/ref without relying on pip's git clone into /tmp.",
    )
    update.add_argument(
        "--branch",
        metavar="REF",
        help="Install from a GitHub ref (branch, tag, or commit SHA) instead of PyPI.",
    )
    update.add_argument(
        "--check",
        action="store_true",
        help="Check for updates without installing.",
    )
    update.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompt.",
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
        if key == "last_run_id":
            raise ValueError(
                "last_run_id is owned by the CLI; omit -e last_run_id= (the wrapper already generates one)."
            )
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
    if args.target:
        return None
    args.commit = True
    return None


def _wants_sandbox(extras: dict) -> bool:
    """True when this run will create an OpenShell sandbox."""
    if extras.get("use_sandbox") is False:
        return False
    if running_inside_openshell() and extras.get("use_sandbox") is not True:
        return False
    if extras.get("use_sandbox") is True:
        return True
    return extras.get("review_type") != "commit"


def _apply_sandbox_fallback(args: argparse.Namespace, extras: dict) -> str | None:
    """Fail closed when a PR/branch review cannot create the default sandbox."""
    if extras.get("review_type") == "commit" and "use_sandbox" not in extras:
        return None
    if extras.get("use_sandbox") is True and not openshell_available():
        return (
            "OpenShell SDK is not importable from "
            f"{sys.executable}. Pass --no-sandbox to review on this interpreter."
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
    return (
        "OpenShell SDK is not importable from "
        f"{sys.executable}. PR/branch reviews run untrusted checklist "
        "commands and require a sandbox. Pass --no-sandbox to review on "
        "this interpreter."
    )


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
        repo = sanitize_display_line(args.repo_path or ".")
        sha = sanitize_display_line(args.commit_sha or "HEAD")
        return f"Reviewing commit {sha} in {repo}\n"
    if args.branch_target:
        return f"Reviewing branch {sanitize_display_line(args.branch_target)}\n"
    return f"Reviewing {sanitize_display_line(args.target)}\n"


def cmd_review(args: argparse.Namespace) -> int:
    error = _validate_review_args(args)
    if error:
        print(error, file=sys.stderr)
        return 2

    try:
        root = Path(args.playbook_root).expanduser().resolve() if args.playbook_root else find_playbook_root()
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
    if _wants_sandbox(extras):
        try:
            reexec_sandbox_runtime(stderr=sys.stderr)
        except OpenshellSdkError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    try:
        ensure_collections(root, stderr=sys.stderr)
    except CollectionInstallError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if getattr(args, "provider", None) or "agent_family" not in extras:
        try:
            resolve_family(
                cli_family=getattr(args, "provider", None),
                stdin=sys.stdin,
                stderr=sys.stderr,
            )
        except (ValueError, ConfigError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    family = extras.get("agent_family") or load_vars().get("agent_family")
    if not family:
        family = (os.environ.get("ANSIBLE_REVIEW_AGENT_FAMILY") or "").strip() or None
    if family:
        try:
            ensure_provider_sdk(str(family), stderr=sys.stderr)
        except ProviderSdkError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    sandbox_error = _apply_sandbox_fallback(args, extras)
    if sandbox_error:
        print(sandbox_error, file=sys.stderr)
        return 2
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
    inherit_tty = (not structured) and ansible_verbosity(args) > 0
    try:
        if inherit_tty:
            sys.stderr.write(_progress_line(args))
            sys.stderr.flush()
            result = run_ansible_playbook(command, playbook_root=root, verbose=True)
        elif spinner_enabled(sys.stderr):
            progress_dir, progress_path = create_progress_file(directory=str(runtime_tmp_dir()))
            try:
                with WaitSpinner(
                    _progress_line(args).rstrip("\n"),
                    stream=sys.stderr,
                    progress_file=progress_path,
                ):
                    result = run_ansible_playbook(
                        command,
                        playbook_root=root,
                        verbose=False,
                        env={"PLAIBOOK_PROGRESS_FILE": progress_path},
                    )
            finally:
                Path(progress_path).unlink(missing_ok=True)
                try:
                    Path(progress_dir).rmdir()
                except OSError:
                    pass
        else:
            if not structured:
                sys.stderr.write(_progress_line(args))
                sys.stderr.flush()
            result = run_ansible_playbook(command, playbook_root=root, verbose=False)
    except (PlaybookTimeoutError, ScratchDirError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    captured = "" if inherit_tty else ((result.stderr or "") + (result.stdout or ""))
    if structured and ansible_verbosity(args) > 0 and captured.strip():
        sys.stderr.write(captured)
        if not captured.endswith("\n"):
            sys.stderr.write("\n")
        captured = ""
    # Always last_run.<run_id>.json — never last_run.json. The playbook
    # always-block writes both (including same-commit cache hits); the
    # canonical path is last-write-wins and can belong to another run.
    summary_file = last_run_path(run_id)
    if not summary_file.is_file():
        if captured.strip():
            sys.stderr.write(captured)
            if not captured.endswith("\n"):
                sys.stderr.write("\n")
        print(
            f"ansible-playbook exited {result.returncode} without writing {summary_file}",
            file=sys.stderr,
        )
        return result.returncode if result.returncode else 2

    try:
        document = enrich_last_run(load_json(summary_file), last_run_file=summary_file)
    except SummaryError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _emit_summary(document, args)
    return result.returncode


def cmd_update(args: argparse.Namespace) -> int:
    """Handle the update command for PyPI and GitHub branch installs."""
    current = current_version()

    # Check mode: just report available version
    if args.check:
        if args.branch:
            print(f"Current version: {current}", file=sys.stderr)
            print(
                f"--check with --branch would install from GitHub ref: {args.branch}",
                file=sys.stderr,
            )
            return 0

        try:
            latest = fetch_pypi_latest_version()
        except NetworkError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        print(f"Current version: {current}", file=sys.stderr)
        print(f"Latest version:  {latest}", file=sys.stderr)
        if current == latest:
            print("plaibook is already up to date.", file=sys.stderr)
            return 0
        else:
            print(f"Update available: {current} → {latest}", file=sys.stderr)
            return 0

    # GitHub branch install (issue #61: use git+https, not tarball)
    if args.branch:
        try:
            if not args.yes:
                if not prompt_confirm(
                    f"Install plaibook from git ref '{args.branch}'?"
                ):
                    print("Update cancelled.", file=sys.stderr)
                    return 1

            print(f"Installing plaibook from git ref: {args.branch}", file=sys.stderr)
            print("(pip will clone from git+https://github.com/aknochow/ansible-plaibook.git)", file=sys.stderr)

            from plaibook.update import _exclusive_update_lock
            with _exclusive_update_lock():
                result = pip_install_git_ref(args.branch, stderr=sys.stderr)

                if result.returncode != 0:
                    print("pip install failed:", file=sys.stderr)
                    stderr_tail = result.stderr[-2000:] if result.stderr else ""
                    if stderr_tail:
                        print(stderr_tail, file=sys.stderr)
                    return 2

                if not verify_installation():
                    print(
                        "Installation completed but version verification failed. "
                        "Please check installation.",
                        file=sys.stderr,
                    )
                    return 2

                # Get the installed version for success message
                try:
                    from importlib.metadata import version
                    installed_version = version("plaibook")
                    print(
                        f"Successfully installed plaibook from '{args.branch}' (version {installed_version})",
                        file=sys.stderr,
                    )
                except Exception:
                    print(
                        f"Successfully installed plaibook from '{args.branch}'",
                        file=sys.stderr,
                    )
                return 0

        except (UpdateError, NetworkError, VersionError) as exc:
            print(str(exc), file=sys.stderr)
            return 2

    # PyPI install (default)
    try:
        latest = fetch_pypi_latest_version()
    except NetworkError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "Cannot check for updates. Use --branch to install from GitHub instead.",
            file=sys.stderr,
        )
        return 2

    if current == latest:
        print(f"plaibook is already up to date ({current}).", file=sys.stderr)
        return 0

    try:
        cache_dir = update_cache_dir()
        print(f"Update available: {current} → {latest}", file=sys.stderr)

        from plaibook.update import _exclusive_update_lock
        with _exclusive_update_lock():
            if not args.yes:
                if not prompt_confirm(f"Update plaibook from {current} to {latest}?"):
                    print("Update cancelled.", file=sys.stderr)
                    return 1

            print(f"Downloading plaibook {latest} from PyPI...", file=sys.stderr)
            wheel_path = download_pypi_wheel(latest, cache_dir)

            print(f"Installing plaibook {latest}...", file=sys.stderr)
            result = pip_install_from_path(wheel_path, stderr=sys.stderr)

            if result.returncode != 0:
                print("pip install failed:", file=sys.stderr)
                stderr_tail = result.stderr[-2000:] if result.stderr else ""
                if stderr_tail:
                    print(stderr_tail, file=sys.stderr)
                return 2

            if not verify_installation(latest):
                print(
                    f"Installation completed but version verification failed. "
                    f"Expected {latest}, please check installation.",
                    file=sys.stderr,
                )
                return 2

            print(f"Successfully updated plaibook to {latest}", file=sys.stderr)
            return 0

    except (UpdateError, NetworkError, VersionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "review":
        return cmd_review(args)
    elif args.command == "update":
        return cmd_update(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
