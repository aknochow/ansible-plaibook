# -*- coding: utf-8 -*-
"""Behavioral tests for the plaibook/plai CLI wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from plaibook.cli import (
    build_parser,
    cmd_review,
    extra_vars_from_args,
    main,
)
from plaibook.playbook import (
    build_ansible_command,
    find_playbook_root,
    generate_run_id,
    last_run_path,
)
from plaibook.summary import enrich_last_run, format_pretty


def _args(**overrides):
    defaults = dict(
        target=None,
        commit=False,
        branch_target=None,
        repo_path=None,
        commit_sha=None,
        verbose=False,
        as_json=False,
        as_yaml=False,
        review_extra_notes=None,
        post=False,
        fail_on_regressions=None,
        playbook_root=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_parser_plai_help_identifies_plaibook():
    parser = build_parser(prog="plai")
    help_text = parser.format_help()
    assert "plaibook CLI" in help_text
    assert "plai" in help_text


def test_plaibook_and_plai_share_the_same_main():
    import plaibook.cli as cli

    parser_a = cli.build_parser(prog="plai")
    parser_b = cli.build_parser(prog="plaibook")
    assert parser_a.parse_args(["review", "--commit"]).commit is True
    assert parser_b.parse_args(["review", "--commit"]).commit is True
    assert cli.main is cli.main


def test_extra_vars_commit_and_pr_and_notes():
    commit = extra_vars_from_args(
        _args(commit=True, repo_path="/tmp/repo", commit_sha="abc1234"),
        "runId0123456789",
    )
    assert commit == {
        "last_run_id": "runId0123456789",
        "review_type": "commit",
        "repo_path": "/tmp/repo",
        "commit_sha": "abc1234",
    }
    pr = extra_vars_from_args(
        _args(target="org/repo#123", review_extra_notes="Note: intentional", post=True),
        "runId0123456789",
    )
    assert pr["review_type"] == "pr"
    assert pr["review_targets_raw"] == "org/repo#123"
    assert pr["review_extra_notes"] == "Note: intentional"
    assert pr["post_results"] is True
    branch = extra_vars_from_args(_args(branch_target="org/repo@main"), "abc")
    assert branch["review_type"] == "branch"
    assert branch["branch_review_target"] == "org/repo@main"


def test_json_extra_vars_keep_colons():
    extras = extra_vars_from_args(
        _args(target="org/repo#1", review_extra_notes="Note: this is intentional"),
        "idididididididid",
    )
    encoded = json.dumps(extras)
    assert "Note: this is intentional" in encoded
    command = build_ansible_command(
        extra_vars=extras,
        playbook_root=Path("/tmp/checkout"),
        ansible_bin="ansible-playbook",
    )
    assert command[0] == "ansible-playbook"
    assert command[1].endswith("review.yml")
    payload = json.loads(command[3])
    assert payload["review_extra_notes"] == "Note: this is intentional"
    assert payload["last_run_id"] == "idididididididid"


def test_find_playbook_root_prefers_env(tmp_path, monkeypatch):
    checkout = tmp_path / "ansible-plaibook"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    found = find_playbook_root(start=other, env={"PLAIBOOK_ROOT": str(checkout)})
    assert found == checkout.resolve()


def test_generate_run_id_shape():
    run_id = generate_run_id()
    assert len(run_id) == 16
    assert run_id.isalnum()
    assert last_run_path("abc").name == "last_run.abc.json"


def test_pretty_and_json_from_last_run(tmp_path):
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "target": "org/repo#1",
                "verdict": "NEEDS_CHANGES",
                "score_overall": 66.7,
                "scores": {"functionality": 80.0, "security": 50.0, "quality": 70.0},
                "findings_count": {"critical": 0, "major": 1, "minor": 2, "nit": 0},
                "findings": [{"severity": "Major", "file": "x.py"}],
            }
        )
    )
    last_run_file = tmp_path / "last_run.deadbeefdeadbeef.json"
    last_run = {
        "run_id": "deadbeefdeadbeef",
        "status": "ok",
        "cost_usd": 0.1234,
        "targets": [
            {
                "target": "org/repo#1",
                "verdict": "NEEDS_CHANGES",
                "score": 66.7,
                "summary_path": str(summary),
                "findings_path": str(tmp_path / "findings.md"),
            }
        ],
    }
    last_run_file.write_text(json.dumps(last_run))
    document = enrich_last_run(json.loads(last_run_file.read_text()), last_run_file=last_run_file)
    pretty = format_pretty(document)
    assert "NEEDS_CHANGES" in pretty
    assert "66.7%" in pretty
    assert "security 50.0%" in pretty
    assert "1 major" in pretty
    assert "last_run:" in pretty
    assert document["targets"][0]["findings"][0]["severity"] == "Major"


def test_cmd_review_quiet_json_yaml_and_exit(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    cache = home / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)

    captured = {}

    def fake_run(command, *, playbook_root, verbose):
        captured["command"] = command
        captured["verbose"] = verbose
        extras = json.loads(command[3])
        path = last_run_path(extras["last_run_id"], home=home)
        path.write_text(
            json.dumps(
                {
                    "run_id": extras["last_run_id"],
                    "status": "ok",
                    "cost_usd": 0.0,
                    "targets": [
                        {
                            "target": "local@HEAD",
                            "verdict": "NEEDS_CHANGES",
                            "score": 40.0,
                        }
                    ],
                }
            )
        )
        return SimpleNamespace(returncode=2, stdout="TASK [noisy]\n", stderr="")

    monkeypatch.setattr("plaibook.cli.find_playbook_root", lambda: checkout)
    monkeypatch.setattr(
        "plaibook.cli.build_ansible_command",
        lambda **kwargs: build_ansible_command(ansible_bin="ansible-playbook", **kwargs),
    )
    monkeypatch.setattr("plaibook.cli.run_ansible_playbook", fake_run)
    monkeypatch.setattr("plaibook.cli.last_run_path", lambda run_id: last_run_path(run_id, home=home))

    code = cmd_review(_args(commit=True, playbook_root=str(checkout)))
    out = capsys.readouterr()
    assert code == 2
    assert captured["verbose"] is False
    assert "TASK [noisy]" not in out.out
    assert "NEEDS_CHANGES" in out.out
    assert "40.0%" in out.out

    capsys.readouterr()
    code = cmd_review(_args(commit=True, playbook_root=str(checkout), as_json=True))
    out = capsys.readouterr()
    assert code == 2
    payload = json.loads(out.out)
    assert payload["targets"][0]["verdict"] == "NEEDS_CHANGES"
    assert "NEEDS_CHANGES" not in out.err or payload["run_id"]

    capsys.readouterr()
    code = cmd_review(_args(commit=True, playbook_root=str(checkout), as_yaml=True))
    out = capsys.readouterr()
    assert code == 2
    parsed = yaml.safe_load(out.out)
    assert parsed["targets"][0]["score"] == 40.0

    capsys.readouterr()
    code = cmd_review(_args(commit=True, playbook_root=str(checkout), verbose=True))
    assert captured["verbose"] is True
    assert code == 2


def test_main_plai_and_plaibook_commit_match(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    (home / ".cache" / "ansible-plaibook").mkdir(parents=True)

    def fake_run(command, *, playbook_root, verbose):
        extras = json.loads(command[3])
        path = last_run_path(extras["last_run_id"], home=home)
        path.write_text(
            json.dumps(
                {
                    "run_id": extras["last_run_id"],
                    "targets": [{"target": "x", "verdict": "READY_FOR_HUMAN_REVIEW", "score": 100}],
                    "cost_usd": 0,
                }
            )
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.cli.find_playbook_root", lambda: checkout)
    monkeypatch.setattr(
        "plaibook.cli.build_ansible_command",
        lambda **kwargs: build_ansible_command(ansible_bin="ansible-playbook", **kwargs),
    )
    monkeypatch.setattr("plaibook.cli.run_ansible_playbook", fake_run)
    monkeypatch.setattr("plaibook.cli.last_run_path", lambda run_id: last_run_path(run_id, home=home))
    monkeypatch.setattr("plaibook.cli.generate_run_id", lambda: "fixedRunId000001")

    argv = ["review", "--commit", "--root", str(checkout)]
    assert main(argv) == 0
    plai_out = capsys.readouterr().out
    assert main(argv) == 0
    plaibook_out = capsys.readouterr().out
    assert plai_out == plaibook_out
    assert "READY_FOR_HUMAN_REVIEW" in plai_out


def test_quiet_dumps_ansible_output_when_last_run_missing(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    (home / ".cache" / "ansible-plaibook").mkdir(parents=True)

    def fake_run(command, *, playbook_root, verbose):
        return SimpleNamespace(returncode=1, stdout="PLAY [boom]\n", stderr="ERROR: nope\n")

    monkeypatch.setattr("plaibook.cli.run_ansible_playbook", fake_run)
    monkeypatch.setattr(
        "plaibook.cli.build_ansible_command",
        lambda **kwargs: build_ansible_command(ansible_bin="ansible-playbook", **kwargs),
    )
    monkeypatch.setattr("plaibook.cli.last_run_path", lambda run_id: last_run_path(run_id, home=home))

    code = cmd_review(_args(commit=True, playbook_root=str(checkout)))
    err = capsys.readouterr().err
    assert code == 1
    assert "PLAY [boom]" in err
    assert "ERROR: nope" in err
