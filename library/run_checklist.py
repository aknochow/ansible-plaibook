#!/usr/bin/python
# -*- coding: utf-8 -*-
# Shebang is deliberately #!/usr/bin/python, not #!/usr/bin/env python3:
# Ansible's own module-shipping mechanism only rewrites this exact form
# to the discovered ansible_python_interpreter for the target host
# before executing a copied-over module. #!/usr/bin/env python3 isn't
# recognized as a substitution target and gets shipped/executed
# verbatim, which fails on a host with no python3 directly on PATH.
r"""Executes a project's CHECKLIST.md against a diff's source ref
inside an isolated git worktree, with a strict command allowlist and
symlink-escape protection.

A real Ansible module, not an action plugin: this needs
delegate_to: review_delegate_host to actually execute on the delegated
host, since checklist commands are parsed out of git content and run
against untrusted PR-branch code. An action plugin with
_requires_connection = False always runs on the controller regardless
of delegate_to, which would silently run allowlisted-but-still-
attacker-influenced commands on the operator's own machine instead of
the sandbox. A real module gets copied to and executed on whichever
host delegate_to resolves to, via Ansible's normal connection-plugin
machinery.

One module invocation owns the whole worktree lifecycle in a Python
try/finally, rather than a multi-task Ansible block/rescue/always:
Ansible's rescue/always only fire on task FAILED results, not
UNREACHABLE ones, so a multi-task teardown delegated to a sandbox is
exposed to an SSH drop mid-run silently skipping teardown and leaking
a worktree with untrusted, symlink-scanned PR-branch content in it. A
Python try/finally inside one module invocation only needs the
connection to survive long enough for this module's own process to
run to completion, a much narrower window.

Three security properties:
  1. CHECKLIST.md is read from the target ref only, never source: an
     untrusted PR/MR branch can't inject its own checklist commands by
     modifying CHECKLIST.md in the branch being reviewed.
  2. A full command allowlist (is_safe_checklist_cmd): binary
     allowlist, git-subcommand allowlist, '..'/absolute/home-path
     rejection, and a dangerous-flag blocklist. See
     test_run_checklist.py for adversarial coverage, not just
     equivalence coverage.
  3. Worktree isolation: git worktree add --detach into a fresh
     tempdir, all symlinks removed (not just ones resolving outside
     the worktree), with an explicit re-verify that zero remain
     immediately before each command executes (skipping the command if
     any have reappeared), then guaranteed teardown in finally: (git
     worktree remove --force, with a shutil.rmtree + git worktree
     prune fallback if that itself fails).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile

from ansible.module_utils.basic import AnsibleModule

MAX_CHECKLIST_COMMANDS = 30

_ALLOWED_BINARIES = frozenset({"grep", "test", "wc", "head", "tail", "bash", "diff"})
_GIT_ALLOWED_SUBCOMMANDS = frozenset({
    "show", "diff", "log", "status", "grep",
    "rev-parse", "symbolic-ref", "ls-files",
})
_DANGEROUS_FLAGS = frozenset({
    "-delete", "-exec", "-execdir", "-ok",
    "--global", "-rf", "-fr", "--force", "--hard",
    "-O", "--upload-pack", "-c", "--exec",
    "-fprintf", "-fls",
    # git grep's own inline-pager flag is a distinct long-option
    # spelling from "-O" (not caught by the "-O" prefix match) that
    # directly executes an attacker-specified program: a checklist
    # command reading a PR-branch-controlled file via
    # `git grep --open-files-in-pager=/bin/sh -- <pattern>` runs that
    # file's contents as a shell script, full arbitrary code execution.
    "--open-files-in-pager",
    # External diff/textconv drivers need a driver name -> command
    # mapping the attacker does not control (must already exist in the
    # reviewing host's own .git/config or ~/.gitconfig) to actually run
    # anything, lower severity than the grep pager flag, but blocked
    # anyway since "allow an external diff helper to be executed" is
    # exactly the class of behavior this allowlist exists to prevent.
    "--ext-diff", "--allow-unsafe-external-diff", "--textconv",
})
_CHECKLIST_FILENAMES = ("CHECKLIST.md", "review-checklist.md")


def _flag_and_value(arg: str) -> tuple[str, str | None]:
    """Split a "--flag=value"-style argument into (flag, value); returns
    (arg, None) if there's no '='. Lets is_safe_checklist_cmd validate a
    flag's NAME and any inline VALUE independently.
    """
    flag_part, sep, value_part = arg.partition("=")
    return (flag_part, value_part) if sep else (arg, None)


def is_safe_checklist_cmd(argv: list[str]) -> bool:
    """Validate a parsed checklist command is safe to execute.

    See test_run_checklist.py for the required adversarial coverage.
    """
    if not argv:
        return False
    # argv[0] is checked via os.path.basename() against the allowlist,
    # but that alone isn't enough: a checklist command written as
    # "./grep ..." or "/some/path/grep ..." would resolve
    # os.path.basename() to the allowed name "grep" while actually
    # executing whatever file lives at that path. Requiring a bare name
    # (no '/' at all) costs nothing: every allowed binary is invoked by
    # bare name in every real example this feature has ever used, and
    # subprocess.run without shell=True already resolves a bare name
    # via PATH only, never via cwd, matching this restriction exactly.
    if "/" in argv[0]:
        return False
    binary = os.path.basename(argv[0])
    if binary not in _ALLOWED_BINARIES and binary != "git":
        return False
    if binary == "bash" and argv[1:2] != ["-n"]:
        return False
    if binary == "git":
        if len(argv) < 2 or argv[1] not in _GIT_ALLOWED_SUBCOMMANDS:
            return False

    for arg in argv[1:]:
        flag_part, value_part = _flag_and_value(arg)

        # Path-traversal / absolute / home-relative checks, applied to
        # the whole arg and, separately, to just the value portion of a
        # "--flag=value" argument: checking only the whole arg misses
        # "--output=../pwned" (splitting that on os.sep gives
        # ["--output..", "pwned"], and neither segment is the exact
        # string ".."), even though the value alone ("../pwned")
        # plainly is path traversal.
        candidates = (arg, value_part) if value_part is not None else (arg,)
        for candidate in candidates:
            if any(part == ".." for part in candidate.split(os.sep)):
                return False
            if candidate.startswith("~") or candidate.startswith("/"):
                return False

        # Dangerous-flags check: exact-string matching against the
        # whole arg misses a GNU "--flag=value" spelling
        # ("--upload-pack=/bin/sh"). Matching on flag_part (everything
        # before the first '=') closes that, while
        # still preserving the original's "-O" prefix-match special case
        # for a bare "-O..." arg (which has no '=' to split on, so
        # flag_part == arg unchanged for it).
        if flag_part in _DANGEROUS_FLAGS or flag_part.startswith("-O"):
            return False

    return True


def parse_checklist_blocks(content: str) -> list[dict]:
    """Parse fenced code blocks (optionally preceded by a '# label' line) into
    a flat list of {'label': str, 'cmd': str} dicts, one per non-empty,
    non-comment line inside a block -- mirrors run_project_checklist's own
    inline parsing loop exactly, extracted as its own pure function so it's
    independently testable.
    """
    entries = []
    in_block = False
    block_label = ""
    commands: list[str] = []

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") and not in_block:
            in_block = True
            continue
        if stripped.startswith("```") and in_block:
            in_block = False
            for raw_cmd in commands:
                cmd = raw_cmd.strip()
                if not cmd or cmd.startswith("#"):
                    continue
                entries.append({"label": block_label or cmd[:60], "cmd": cmd})
            commands = []
            block_label = ""
            continue
        if in_block:
            commands.append(line)
        elif stripped.startswith("#"):
            block_label = stripped.lstrip("#").strip()[:60]

    return entries


def find_all_symlinks(root_dir: str) -> list[str]:
    """Return every symlink under root_dir, escaping or not -- pure
    detection, no filesystem mutation, so the caller can log/test what
    would be removed (or verify none remain) before mutating anything.

    Every symlink is in scope, not just ones whose real target resolves
    outside root_dir: checklist verification commands (grep/test/wc/
    head/tail/bash -n/git show/diff/...) don't need symlinks at all, so
    a structural invariant ("this worktree has no symlinks, full stop")
    is simpler and doesn't go stale the way "only symlinks that escape
    the worktree are unsafe" could if the allowlist ever grows.
    followlinks=False on os.walk so a symlinked directory is reported
    itself, never descended into and had its own contents enumerated
    as if they were real worktree paths.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(root_dir, followlinks=False):
        for name in filenames + dirnames:
            fpath = os.path.join(dirpath, name)
            if os.path.islink(fpath):
                found.append(fpath)
    return found


def remove_all_symlinks(root_dir: str) -> list[str]:
    """Remove every symlink found by find_all_symlinks; returns the list
    of paths actually removed.
    """
    removed = []
    for fpath in find_all_symlinks(root_dir):
        os.unlink(fpath)
        removed.append(fpath)
    return removed


def _run_cmd(argv: list[str], cwd: str, timeout: int) -> tuple[bool, str]:
    """Run argv with a timeout; returns (success, stdout-or-stderr,
    stripped). On FileNotFoundError/TimeoutExpired/OSError, returns
    (False, <diagnostic string>) instead of (False, "") -- a checklist
    result's own output field otherwise couldn't distinguish "the
    binary isn't on this host," "it timed out," or a real non-zero exit
    from each other (all showed as "(empty)"), a real diagnostic-loss
    finding from this MR's own dogfood round.
    """
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        return result.returncode == 0, (result.stdout or result.stderr or "").strip()
    except FileNotFoundError:
        return False, "(binary not found)"
    except subprocess.TimeoutExpired:
        return False, f"(timed out after {timeout}s)"
    except OSError as exc:
        return False, f"(OS error: {exc})"


def read_checklist_content(repo_path: str, target_ref: str) -> str | None:
    """Read CHECKLIST.md (or review-checklist.md) from the TARGET ref only
    -- never source_ref. This IS the trust boundary: an untrusted PR/MR
    branch modifying CHECKLIST.md in the branch being reviewed must never
    be able to inject its own checklist commands.
    """
    for filename in _CHECKLIST_FILENAMES:
        ok, content = _run_cmd(["git", "show", f"{target_ref}:{filename}"], cwd=repo_path, timeout=10)
        if ok and content:
            return content
    return None


def run_checklist(repo_path: str, target_ref: str, source_ref: str) -> dict:
    """Full lifecycle: read CHECKLIST.md from target_ref, create a detached
    worktree of source_ref, remove all symlinks and verify none remain
    before each command, run allowlisted commands, guarantee teardown in
    finally:. Mirrors prepare_briefing.py's run_project_checklist()
    structure closely.
    """
    content = read_checklist_content(repo_path, target_ref)
    if content is None:
        return {"triggered": False, "results": []}

    worktree_dir = tempfile.mkdtemp(prefix="review-checklist-")
    ok, _ = _run_cmd(["git", "worktree", "add", "--detach", worktree_dir, source_ref], cwd=repo_path, timeout=15)
    if not ok:
        try:
            os.rmdir(worktree_dir)
        except OSError:
            pass
        return {"triggered": True, "results": [], "warning": "worktree creation failed, skipping checklist"}

    results = []
    try:
        executed = 0
        for entry in parse_checklist_blocks(content):
            if executed >= MAX_CHECKLIST_COMMANDS:
                break
            try:
                argv = shlex.split(entry["cmd"])
            except ValueError:
                continue
            if not is_safe_checklist_cmd(argv):
                continue
            # Re-scanned before every command, not once before the
            # loop: closes a TOCTOU window where a symlink could
            # theoretically be re-created between the scan and a later
            # command's execution. Every allowed binary/git-subcommand
            # is strictly read-only today, so nothing in this function's
            # own control could recreate a symlink, but that's exactly
            # the kind of assumption that goes stale as the allowlist
            # evolves. Verify the actual invariant (no symlinks exist)
            # instead of trusting that removal succeeded, and skip the
            # command if one has reappeared. There is deliberately no
            # result entry recorded for a skip: a "SKIPPED" line in a
            # human-facing findings.md report for a condition that
            # structurally cannot occur yet would raise more questions
            # than it answers. The check stays here as a structural
            # guarantee for if that ever changes.
            #
            # executed IS incremented on a skip (unlike the shlex/
            # is_safe_checklist_cmd continues above, which are free and
            # bounded by CHECKLIST.md's own trusted content) --
            # confirmed live via review: remove_all_symlinks/
            # find_all_symlinks each do a full os.walk of the worktree,
            # so leaving a skip's cost off the budget would let a run
            # with many skipped entries perform far more filesystem
            # work than MAX_CHECKLIST_COMMANDS is meant to bound, even
            # though CHECKLIST.md itself isn't attacker-controlled.
            remove_all_symlinks(worktree_dir)
            executed += 1
            if find_all_symlinks(worktree_dir):
                continue
            ok, output = _run_cmd(argv, cwd=worktree_dir, timeout=10)
            result = "PASS" if ok else "FAIL"
            if not output and ok:
                result = "PASS (no output)"
            results.append({
                "check": entry["label"],
                "command": entry["cmd"][:120],
                "output": output[:200] if output else "(empty)",
                "result": result,
            })
    finally:
        ok, _ = _run_cmd(["git", "worktree", "remove", "--force", worktree_dir], cwd=repo_path, timeout=10)
        if not ok:
            shutil.rmtree(worktree_dir, ignore_errors=True)
            _run_cmd(["git", "worktree", "prune"], cwd=repo_path, timeout=10)

    return {"triggered": True, "results": results}


def main():
    module = AnsibleModule(
        argument_spec=dict(
            repo_path=dict(type="str", required=True),
            target_ref=dict(type="str", required=True),
            source_ref=dict(type="str", required=True),
        ),
        supports_check_mode=False,
    )

    # Worktree teardown itself is guaranteed by run_checklist's own
    # try/finally regardless of what happens here (confirmed by
    # test_worktree_teardown_happens_even_if_a_command_raises_unexpectedly)
    # -- this try/except is only about how an unexpected exception,
    # AFTER teardown has already run, gets reported: a real dogfood
    # finding correctly caught that an uncaught exception here would
    # propagate as a raw Python traceback (an Ansible "module failed
    # unexpectedly" wall of text) instead of a clean, structured
    # module.fail_json() result.
    try:
        outcome = run_checklist(
            module.params["repo_path"],
            module.params["target_ref"],
            module.params["source_ref"],
        )
    except Exception as exc:
        module.fail_json(msg=f"run_checklist failed unexpectedly: {exc}")
        return

    module.exit_json(changed=False, **outcome)


if __name__ == "__main__":
    main()
