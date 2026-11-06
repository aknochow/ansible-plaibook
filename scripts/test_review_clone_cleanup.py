# -*- coding: utf-8 -*-
"""Review clones are removed however the run ends."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "roles" / "review" / "tasks"


def _walk(items):
    for item in items or []:
        yield item
        for key in ("block", "rescue", "always"):
            yield from _walk(item.get(key))


def _guarded_block(name: str, clone_var: str) -> dict:
    """The block whose `block` clones and runs the engine and whose `always` deletes the clone."""
    tasks = list(_walk(yaml.safe_load((TASKS / name).read_text(encoding="utf-8"))))
    for task in tasks:
        body = list(_walk(task.get("block")))
        if any("ansible.builtin.git" in item for item in body) and task.get("always"):
            assert any(
                item.get("ansible.builtin.include_tasks") == "run_review_engine.yml" for item in body
            ), "the review engine must run inside the guarded block"
            return task
    pytest.fail(f"{name}: no block clones the target with an always cleanup")


@pytest.mark.parametrize(
    "name, clone_var",
    [
        ("resolve_target_pr.yml", "review_clone_dir_controller"),
        ("resolve_target_branch.yml", "branch_review_checkout_dir"),
    ],
)
def test_the_clone_is_removed_in_always(name, clone_var):
    block = _guarded_block(name, clone_var)
    removes = [
        task
        for task in block["always"]
        if (task.get("ansible.builtin.file") or {}).get("state") == "absent"
    ]
    assert len(removes) == 1
    assert clone_var in removes[0]["ansible.builtin.file"]["path"]
    assert "review_cleanup_clone | bool" in removes[0]["when"]


def test_no_cleanup_is_left_on_the_success_path_only():
    tasks = list(_walk(yaml.safe_load((TASKS / "resolve_target_pr.yml").read_text(encoding="utf-8"))))
    always = {id(item) for task in tasks for item in _walk(task.get("always"))}
    for task in tasks:
        file_args = task.get("ansible.builtin.file") or {}
        if file_args.get("state") == "absent" and "checkout" in str(file_args.get("path")):
            assert id(task) in always, f"cleanup outside always: {task.get('name')}"


def test_default_keeps_cleanup_on():
    defaults = yaml.safe_load((ROOT / "roles" / "review" / "defaults" / "main.yml").read_text(encoding="utf-8"))
    assert defaults["review_cleanup_clone"] is True


# plai review: the run's own scratch directory


def _review_setup(tmp_path, monkeypatch, fake_run):
    from plaibook.playbook import build_ansible_command, last_run_path

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    (home / ".cache" / "ansible-plaibook").mkdir(parents=True)
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr("plaibook.cli.run_ansible_playbook", fake_run)
    monkeypatch.setattr(
        "plaibook.cli.build_ansible_command",
        lambda **kwargs: build_ansible_command(ansible_bin="ansible-playbook", **kwargs),
    )
    monkeypatch.setattr("plaibook.cli.last_run_path", lambda run_id: last_run_path(run_id, home=home))
    monkeypatch.setattr("plaibook.cli.runtime_tmp_dir", lambda home=None: scratch_root)
    return checkout, home, scratch_root


def _args(**overrides):
    values = dict(
        target="org/repo/1",
        commit=False,
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
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _write_summary(command, home):
    from plaibook.playbook import last_run_path

    extras = json.loads(command[command.index("-e") + 1])
    path = last_run_path(extras["last_run_id"], home=home)
    path.write_text(json.dumps({"run_id": extras["last_run_id"], "status": "ok", "targets": []}))


@pytest.mark.parametrize("outcome", ["ok", "failed", "interrupted"])
def test_the_run_scratch_directory_is_removed_however_the_run_ends(tmp_path, monkeypatch, outcome):
    from plaibook.cli import cmd_review
    from plaibook.playbook import PlaybookInterruptedError

    seen = {}

    def fake_run(command, *, playbook_root, verbose, env=None, tmp_dir=None):
        seen["dir"] = tmp_dir
        assert tmp_dir is not None and tmp_dir.is_dir()
        assert tmp_dir.parent == scratch_root
        (tmp_dir / "review-1-abc").mkdir()
        (tmp_dir / "review-1-abc" / "file").write_text("clone\n")
        if outcome == "interrupted":
            raise PlaybookInterruptedError("Ctrl-C")
        _write_summary(command, home)
        return SimpleNamespace(returncode=0 if outcome == "ok" else 2, stdout="", stderr="")

    checkout, home, scratch_root = _review_setup(tmp_path, monkeypatch, fake_run)
    cmd_review(_args(playbook_root=str(checkout)))
    assert not seen["dir"].exists()
    assert scratch_root.is_dir()


def test_review_cleanup_clone_false_keeps_the_scratch_directory(tmp_path, monkeypatch, capsys):
    from plaibook.cli import cmd_review

    seen = {}

    def fake_run(command, *, playbook_root, verbose, env=None, tmp_dir=None):
        seen["dir"] = tmp_dir
        _write_summary(command, home)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    checkout, home, scratch_root = _review_setup(tmp_path, monkeypatch, fake_run)
    cmd_review(_args(playbook_root=str(checkout), cli_extra_vars=["review_cleanup_clone=false"]))
    assert seen["dir"].is_dir()
    assert str(seen["dir"]) in capsys.readouterr().err


def test_run_ansible_playbook_uses_the_given_tmp_dir(tmp_path, monkeypatch):
    from plaibook import playbook

    seen = {}
    monkeypatch.setattr(playbook, "run_process_group", lambda command, *, env, verbose, timeout: seen.update(env))
    monkeypatch.setattr(playbook, "playbook_timeout_seconds", lambda: 60)
    monkeypatch.setattr("plaibook.collections.merge_collections_path", lambda env, home=None: None)
    playbook.run_ansible_playbook(["true"], playbook_root=tmp_path, verbose=False, tmp_dir=tmp_path / "run")
    assert seen["TMPDIR"] == str(tmp_path / "run")


def test_run_scratch_dir_is_private_and_named_for_the_run(tmp_path):
    from plaibook.playbook import run_scratch_dir

    path = run_scratch_dir("Ab12/../x", tmp_path)
    assert path.parent == tmp_path
    assert path.name.startswith("run-Ab12x-")
    assert (path.stat().st_mode & 0o777) == 0o700
