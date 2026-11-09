# -*- coding: utf-8 -*-
"""Old run files in the plaibook cache are removed; everything else stays."""

from __future__ import annotations

import fcntl
import os
import time
from types import SimpleNamespace

import pytest

from plaibook import retention
from plaibook.retention import prune_run_files, retention_days

NOW = time.time()
OLD = NOW - 20 * 86400
NEW = NOW - 2 * 86400


def _touch(path, mtime, *, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))
    return path


def _dir(path, mtime):
    path.mkdir(parents=True, exist_ok=True)
    _touch(path / "file", mtime)
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def cache(tmp_path):
    root = tmp_path / ".cache" / "ansible-plaibook"
    root.mkdir(parents=True)
    return root


def test_removes_only_old_run_files(tmp_path, cache):
    old_result = _touch(cache / "last_run.AAAAAAAAAAAAAAAA.json", OLD)
    new_result = _touch(cache / "last_run.BBBBBBBBBBBBBBBB.json", NEW)
    old_failures = _dir(cache / "tmp" / "plaibook-task-failures-abc", OLD)
    old_clone = _dir(cache / "tmp" / "review-151-xyz", OLD)
    new_scratch = _dir(cache / "tmp" / "run-Ab12-xyz", NEW)
    old_prompts = _dir(cache / "debug_prompts" / "github-org-repo-1", OLD)

    result = prune_run_files(14, tmp_path, now=NOW)

    assert sorted(result.removed) == sorted([old_result, old_failures, old_clone, old_prompts])
    assert result.errors == []
    assert new_result.exists() and new_scratch.exists()
    assert (cache / "tmp").is_dir() and (cache / "debug_prompts").is_dir()


def test_never_touches_reports_ledger_locks_or_other_cache_files(tmp_path, cache):
    kept = [
        _touch(cache / "last_run.json", OLD),
        _touch(cache / "run_ledger.jsonl", OLD),
        _touch(cache / "collections.lock", OLD),
        _touch(cache / "last_run.short.json", OLD),
        _touch(cache / "ledger" / "host.jsonl", OLD),
        _dir(cache / "collections" / "ansible_collections", OLD),
        _dir(cache / "worktree-main", OLD),
    ]
    _dir(cache / "last_run.CCCCCCCCCCCCCCCC.json", OLD)

    result = prune_run_files(14, tmp_path, now=NOW)

    assert result.removed == []
    assert all(path.exists() for path in kept)
    assert (cache / "last_run.CCCCCCCCCCCCCCCC.json").is_dir()


def test_zero_days_keeps_everything(tmp_path, cache):
    path = _touch(cache / "last_run.AAAAAAAAAAAAAAAA.json", OLD)
    assert prune_run_files(0, tmp_path, now=NOW).removed == []
    assert path.exists()


def test_does_not_follow_or_remove_symlinks(tmp_path, cache):
    outside = _dir(tmp_path / "outside" / "keep", OLD)
    link = cache / "tmp" / "review-1-link"
    link.parent.mkdir()
    link.symlink_to(outside, target_is_directory=True)
    os.utime(link, (OLD, OLD), follow_symlinks=False)
    linked_tmp = tmp_path / "elsewhere"
    _dir(linked_tmp / "review-2-abc", OLD)
    (cache / "debug_prompts").symlink_to(linked_tmp, target_is_directory=True)

    result = prune_run_files(14, tmp_path, now=NOW)

    assert result.removed == []
    assert link.is_symlink() and (outside / "file").exists()
    assert (linked_tmp / "review-2-abc" / "file").exists()


def test_a_symlinked_cache_is_left_alone(tmp_path):
    real = tmp_path / "real"
    _touch(real / "last_run.AAAAAAAAAAAAAAAA.json", OLD)
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "ansible-plaibook").symlink_to(real, target_is_directory=True)
    assert prune_run_files(14, tmp_path, now=NOW).removed == []
    assert (real / "last_run.AAAAAAAAAAAAAAAA.json").exists()


def test_missing_cache_is_fine(tmp_path):
    assert prune_run_files(14, tmp_path, now=NOW).removed == []


def test_a_fresh_file_in_an_old_directory_keeps_the_directory(tmp_path, cache):
    dump = _dir(cache / "debug_prompts" / "github-org-repo-1", OLD)
    _touch(dump / "lens.txt", NEW)
    os.utime(dump, (OLD, OLD))
    assert prune_run_files(14, tmp_path, now=NOW).removed == []
    assert (dump / "lens.txt").exists()


def test_a_symlink_inside_an_old_directory_is_unlinked_not_followed(tmp_path, cache):
    outside = _dir(tmp_path / "outside", OLD)
    clone = _dir(cache / "tmp" / "review-1-abc", OLD)
    (clone / "link").symlink_to(outside, target_is_directory=True)
    os.utime(clone / "link", (OLD, OLD), follow_symlinks=False)
    os.utime(clone, (OLD, OLD))
    assert prune_run_files(14, tmp_path, now=NOW).removed == [clone]
    assert (outside / "file").exists()


needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")


@pytest.fixture
def restore_modes():
    paths = []
    yield paths
    for path in paths:
        os.chmod(path, 0o700)


@needs_permissions
def test_a_removal_error_is_reported_and_the_rest_continue(tmp_path, cache, restore_modes):
    blocked = _dir(cache / "tmp" / "review-1-abc", OLD)
    locked = _dir(blocked / "objects", OLD)
    os.chmod(locked, 0o500)
    restore_modes.append(locked)
    os.utime(blocked, (OLD, OLD))
    removed = _touch(cache / "last_run.AAAAAAAAAAAAAAAA.json", OLD)

    result = prune_run_files(14, tmp_path, now=NOW)
    assert result.removed == [removed]
    assert len(result.errors) == 1 and str(blocked) in result.errors[0]


@needs_permissions
def test_an_unreadable_directory_is_reported_not_raised(tmp_path, cache, restore_modes):
    scratch = cache / "tmp"
    scratch.mkdir()
    os.chmod(scratch, 0o000)
    restore_modes.append(scratch)
    removed = _touch(cache / "last_run.AAAAAAAAAAAAAAAA.json", OLD)

    result = prune_run_files(14, tmp_path, now=NOW)
    assert result.removed == [removed]
    assert len(result.errors) == 1 and str(scratch) in result.errors[0]


def test_a_file_written_after_the_age_check_is_kept(tmp_path, cache, monkeypatch):
    dump = _dir(cache / "debug_prompts" / "github-org-repo-1", OLD)
    stale = _touch(dump / "old-lens.txt", OLD)
    os.utime(dump, (OLD, OLD))

    def is_old_then_write(dir_fd, name, cutoff):
        # A concurrent review writes a dump right after the check passes.
        _touch(dump / "lens.txt", NEW)
        os.utime(dump, (OLD, OLD))
        return True

    monkeypatch.setattr(retention, "_is_old", is_old_then_write)
    result = prune_run_files(14, tmp_path, now=NOW)
    assert result.removed == [] and result.errors == []
    assert (dump / "lens.txt").exists()
    assert not stale.exists()


def test_a_tree_too_deep_to_walk_is_reported_and_skipped(tmp_path, cache, monkeypatch):
    deep = _dir(cache / "tmp" / "review-1-abc", OLD)
    removed = _touch(cache / "last_run.AAAAAAAAAAAAAAAA.json", OLD)
    real = retention._is_old

    def is_old(dir_fd, name, cutoff):
        if name == deep.name:
            raise RecursionError("maximum recursion depth exceeded")
        return real(dir_fd, name, cutoff)

    monkeypatch.setattr(retention, "_is_old", is_old)
    result = prune_run_files(14, tmp_path, now=NOW)
    assert result.removed == [removed]
    assert deep.exists() and str(deep) in result.errors[0]


def test_start_run_prunes_and_holds_a_shared_lock(tmp_path, cache):
    old = _touch(cache / "last_run.AAAAAAAAAAAAAAAA.json", OLD)
    result, fd = retention.start_run(14, tmp_path)
    try:
        assert result.removed == [old] and not result.busy
        other = os.open(cache / retention.RUN_LOCK_NAME, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
        finally:
            os.close(other)
    finally:
        os.close(fd)


def test_start_run_skips_pruning_while_another_review_runs(tmp_path, cache):
    old = _touch(cache / "last_run.AAAAAAAAAAAAAAAA.json", OLD)
    first_result, first = retention.start_run(14, tmp_path)
    _touch(old, OLD)
    try:
        result, fd = retention.start_run(14, tmp_path)
        os.close(fd)
    finally:
        os.close(first)
    assert first_result.removed == [old]
    assert result.busy and result.removed == [] and old.exists()


def test_start_run_without_a_cache_holds_no_lock(tmp_path):
    (tmp_path / ".cache").write_text("not a directory")
    result, fd = retention.start_run(14, tmp_path)
    assert fd is None and result.errors


@pytest.mark.parametrize(
    "extras, operator_vars, expected",
    [
        ({}, {}, 14),
        ({}, {"review_log_retention_days": 30}, 30),
        ({"review_log_retention_days": "0"}, {"review_log_retention_days": 30}, 0),
        ({"review_log_retention_days": 7}, {}, 7),
    ],
)
def test_retention_days_order(extras, operator_vars, expected):
    assert retention_days(extras, operator_vars) == expected


@pytest.mark.parametrize("value", ["-1", "two weeks", True, "1.5"])
def test_retention_days_rejects_bad_values(value):
    with pytest.raises(ValueError, match="review_log_retention_days"):
        retention_days({"review_log_retention_days": value}, {})


def _review_args(**overrides):
    values = dict(
        target=None,
        commit=True,
        branch_target=None,
        repo_path=None,
        commit_sha=None,
        verbose=False,
        as_json=False,
        as_yaml=False,
        full=False,
        force=False,
        debug=False,
        review_extra_notes=None,
        post=False,
        fail_on_regressions=None,
        provider="openai",
        use_sandbox=False,
        cli_extra_vars=None,
        playbook_root=None,
        ee=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_plai_review_prunes_before_the_run(tmp_path, monkeypatch):
    from plaibook import cli

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    calls = []
    monkeypatch.setattr(retention, "start_run", lambda days: (calls.append(days) or retention.PruneResult(), None))
    monkeypatch.setattr(cli, "_cmd_review_ee", lambda *args: calls.append("run") or 0)

    assert cli.cmd_review(_review_args(playbook_root=str(checkout))) == 0
    assert calls == [14, "run"]


def test_plai_review_refuses_a_bad_retention_value(tmp_path, monkeypatch, capsys):
    from plaibook import cli

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(cli, "_cmd_review_ee", lambda *args: pytest.fail("must not run"))

    args = _review_args(playbook_root=str(checkout), cli_extra_vars=["review_log_retention_days=-3"])
    assert cli.cmd_review(args) == 2
    assert "review_log_retention_days must be 0 or more" in capsys.readouterr().err
