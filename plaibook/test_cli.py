# -*- coding: utf-8 -*-
"""Behavioral tests for the plaibook/plai CLI wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from plaibook.cli import (
    ansible_verbosity,
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
        full=False,
        force=False,
        debug=False,
        review_extra_notes=None,
        post=False,
        fail_on_regressions=None,
        provider=None,
        use_sandbox=None,
        cli_extra_vars=None,
        playbook_root=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_parser_plai_help_identifies_plaibook():
    parser = build_parser(prog="plai")
    help_text = parser.format_help()
    assert "plaibook CLI" in help_text
    assert "plai" in help_text
    assert "plai review org/repo#123" in help_text
    assert "plai review --commit" in help_text
    assert "plai review org/repo#123 --json" in help_text
    assert "uv run plai" not in help_text
    assert "/10" not in help_text
    assert "spinner" in help_text.lower()
    assert "lenses" in help_text
    assert "--debug" in help_text
    assert "-vv" in help_text


def test_parser_debug_and_vv_set_ansible_verbosity():
    parser = build_parser(prog="plai")
    quiet = parser.parse_args(["review", "org/repo#1"])
    assert ansible_verbosity(quiet) == 0
    one = parser.parse_args(["review", "org/repo#1", "-v"])
    assert one.verbose == 1
    assert ansible_verbosity(one) == 1
    two = parser.parse_args(["review", "org/repo#1", "-vv"])
    assert two.verbose == 2
    assert ansible_verbosity(two) == 2
    debug = parser.parse_args(["review", "org/repo#1", "--debug"])
    assert debug.debug is True
    assert ansible_verbosity(debug) == 2
    debug_plus = parser.parse_args(["review", "org/repo#1", "-vvv", "--debug"])
    assert ansible_verbosity(debug_plus) == 3


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


def test_extra_vars_sandbox_and_passthrough():
    extras = extra_vars_from_args(
        _args(
            target="org/repo#1",
            use_sandbox=False,
            cli_extra_vars=["review_clone_url_override=file:///tmp/x.git"],
        ),
        "runId0123456789",
    )
    assert extras["use_sandbox"] is False
    assert extras["review_clone_url_override"] == "file:///tmp/x.git"
    extras = extra_vars_from_args(
        _args(target="org/repo#1", cli_extra_vars=["use_sandbox=true"]),
        "runId0123456789",
    )
    assert extras["use_sandbox"] is True


def test_cmd_review_skips_resolve_family_when_agent_family_extra(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    (home / ".cache" / "ansible-plaibook").mkdir(parents=True)
    called = []

    def fake_run(command, *, playbook_root, verbose, env=None):
        extras = json.loads(command[command.index("-e") + 1])
        path = last_run_path(extras["last_run_id"], home=home)
        path.write_text(json.dumps({"run_id": extras["last_run_id"], "status": "ok", "targets": []}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr("plaibook.cli.run_ansible_playbook", fake_run)
    monkeypatch.setattr(
        "plaibook.cli.build_ansible_command",
        lambda **kwargs: build_ansible_command(ansible_bin="ansible-playbook", **kwargs),
    )
    monkeypatch.setattr("plaibook.cli.last_run_path", lambda run_id: last_run_path(run_id, home=home))
    monkeypatch.setattr(
        "plaibook.cli.resolve_family",
        lambda **kwargs: called.append(kwargs),
    )

    code = cmd_review(
        _args(
            commit=True,
            playbook_root=str(checkout),
            cli_extra_vars=["agent_family=gemini"],
        )
    )
    assert code == 0
    assert called == []

    code = cmd_review(_args(commit=True, playbook_root=str(checkout), provider="cursor"))
    assert code == 0
    assert called and called[0]["cli_family"] == "cursor"


def test_extra_vars_force_disables_same_commit_fast_path():
    extras = extra_vars_from_args(_args(target="org/repo#1", force=True), "runId0123456789")
    assert extras["review_same_commit_fast_path_enabled"] is False
    extras = extra_vars_from_args(_args(target="org/repo#1"), "runId0123456789")
    assert "review_same_commit_fast_path_enabled" not in extras


def test_parser_force_short_flag():
    parser = build_parser(prog="plai")
    args = parser.parse_args(["review", "org/repo#1", "-f"])
    assert args.force is True
    help_text = parser.format_help()
    assert "-f" in help_text
    assert "--force" in help_text
    assert "plai review org/repo#123 -f" in help_text


def test_parser_provider_and_no_sandbox():
    parser = build_parser(prog="plai")
    args = parser.parse_args(["review", "org/repo#1", "--no-sandbox", "--provider", "cursor"])
    assert args.use_sandbox is False
    assert args.provider == "cursor"
    help_text = parser.format_help()
    assert "--provider" in help_text
    assert "--no-sandbox" in help_text


def test_persist_cursor_defaults(tmp_path, monkeypatch):
    import os
    from io import StringIO

    from plaibook.config import load_vars, resolve_family

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("ANSIBLE_REVIEW_AGENT_FAMILY", raising=False)
    err = StringIO()
    resolve_family(
        cli_family="cursor",
        stdin=StringIO(""),
        stderr=err,
        env=os.environ,
    )
    saved = load_vars(env=os.environ)
    assert saved["agent_family"] == "cursor"
    assert saved["review_cursor_model"] == "gpt-5.6-luna"
    assert saved["review_cursor_effort"] == "high"
    assert "Saved provider cursor" in err.getvalue()


def test_prompt_saves_typed_family(tmp_path, monkeypatch):
    import os
    from io import StringIO

    from plaibook.config import load_vars, resolve_family

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("ANSIBLE_REVIEW_AGENT_FAMILY", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    err = StringIO()
    resolve_family(
        cli_family=None,
        stdin=StringIO("cursor\n"),
        stderr=err,
        env=os.environ,
        interactive=True,
    )
    saved = load_vars(env=os.environ)
    assert saved["agent_family"] == "cursor"
    assert saved["review_cursor_model"] == "gpt-5.6-luna"


def test_sandbox_fallback_fails_closed_when_sdk_missing(monkeypatch, capsys):
    from plaibook.cli import _apply_sandbox_fallback

    monkeypatch.setattr("plaibook.cli.openshell_available", lambda: False)
    monkeypatch.setattr("plaibook.cli.running_inside_openshell", lambda: False)
    extras = {"review_type": "pr", "review_targets_raw": "org/repo#1"}
    error = _apply_sandbox_fallback(_args(target="org/repo#1"), extras)
    assert error is not None
    assert "require a sandbox" in error
    assert "--no-sandbox" in error
    assert "use_sandbox" not in extras
    assert capsys.readouterr().err == ""


def test_sandbox_fallback_explicit_no_sandbox_when_sdk_missing(monkeypatch):
    from plaibook.cli import _apply_sandbox_fallback

    monkeypatch.setattr("plaibook.cli.openshell_available", lambda: False)
    monkeypatch.setattr("plaibook.cli.running_inside_openshell", lambda: False)
    extras = {"review_type": "pr", "review_targets_raw": "org/repo#1", "use_sandbox": False}
    error = _apply_sandbox_fallback(_args(target="org/repo#1", use_sandbox=False), extras)
    assert error is None
    assert extras["use_sandbox"] is False


def test_sandbox_fallback_quiet_when_already_inside_openshell(monkeypatch, capsys):
    from plaibook.cli import _apply_sandbox_fallback

    monkeypatch.setattr("plaibook.cli.openshell_available", lambda: False)
    monkeypatch.setattr("plaibook.cli.running_inside_openshell", lambda: True)
    extras = {"review_type": "pr", "review_targets_raw": "org/repo#1"}
    error = _apply_sandbox_fallback(_args(target="org/repo#1"), extras)
    assert error is None
    assert extras["use_sandbox"] is False
    assert capsys.readouterr().err == ""


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
    payload = json.loads(command[command.index("-e") + 1])
    assert payload["review_extra_notes"] == "Note: this is intentional"
    assert payload["last_run_id"] == "idididididididid"


def test_build_ansible_command_debug_passes_vv(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    command = build_ansible_command(
        extra_vars={"last_run_id": "abc", "review_type": "pr"},
        playbook_root=checkout,
        ansible_bin="ansible-playbook",
        verbosity=2,
    )
    assert command[0] == "ansible-playbook"
    assert command[1].endswith("review.yml")
    assert command[2] == "-vv"
    assert command[3] == "-e"


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
                "findings": [
                    {
                        "severity": "Major",
                        "file": "plaibook/cli.py",
                        "line": 42,
                        "description": (
                            "Default stdout only prints finding counts. "
                            "A human never sees why the score dropped."
                        ),
                    },
                    {
                        "severity": "Minor",
                        "file": "docs/getting-started.md",
                        "line": 86,
                        "description": "Example score still looks like a /10 scale.",
                    },
                ],
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
    assert "Major  plaibook/cli.py:42" in pretty
    assert "Default stdout only prints finding counts" in pretty
    assert "A human never sees why the score dropped" in pretty
    assert "Example score still looks like a /10 scale." not in pretty
    assert "2 minor" in pretty
    assert "last_run:" in pretty
    assert "findings.md:" in pretty
    assert document["targets"][0]["findings"][0]["severity"] == "Major"
    full = format_pretty(document, full=True)
    assert "full rendered report" not in full  # no report field in this fixture
    document["targets"][0]["report"] = "## Code Review\n\n### Findings\nfull rendered report"
    assert "full rendered report" in format_pretty(document, full=True)
    assert "full rendered report" not in format_pretty(document)


def test_pretty_explains_same_commit_cache_hit():
    pretty = format_pretty(
        {
            "commit": "7c4db4d",
            "cost_usd": 0,
            "status": "ok",
            "targets": [
                {
                    "target": "org/repo#1",
                    "verdict": "READY_FOR_HUMAN_REVIEW",
                    "score": 100.0,
                    "cache_hit": True,
                }
            ],
        }
    )
    assert "same-commit cache hit for 7c4db4d" in pretty
    assert "$0.00 is expected" in pretty
    assert "Re-run with -f to force" in pretty
    assert "$0.0000" in pretty


def test_enrich_prefers_run_scoped_summary_over_canonical(tmp_path):
    canonical = tmp_path / "summary.json"
    scoped = tmp_path / "summary.thisRunOnly0001.json"
    canonical.write_text(
        json.dumps(
            {
                "verdict": "READY_FOR_HUMAN_REVIEW",
                "score_overall": 100.0,
                "scores": {"functionality": 100.0, "security": 100.0, "quality": 100.0},
                "findings_count": {"critical": 0, "major": 0, "minor": 0, "nit": 0},
                "findings": [],
            }
        )
    )
    scoped.write_text(
        json.dumps(
            {
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
        "targets": [
            {
                "target": "org/repo#1",
                "summary_path": str(canonical),
                "summary_run_scoped_path": str(scoped),
            }
        ],
    }
    document = enrich_last_run(last_run, last_run_file=last_run_file)
    target = document["targets"][0]
    assert target["verdict"] == "NEEDS_CHANGES"
    assert target["score_overall"] == 66.7
    assert target["score"] == 66.7
    assert target["findings_count"]["major"] == 1


def test_enrich_skips_canonical_when_advertised_run_scoped_is_missing(tmp_path):
    canonical = tmp_path / "summary.json"
    canonical.write_text(json.dumps({"verdict": "READY_FOR_HUMAN_REVIEW", "score_overall": 100.0}))
    last_run = {
        "targets": [
            {
                "target": "org/repo#1",
                "verdict": "NEEDS_CHANGES",
                "score": 66.7,
                "summary_path": str(canonical),
                "summary_run_scoped_path": str(tmp_path / "summary.missing.json"),
            }
        ],
    }
    document = enrich_last_run(last_run, last_run_file=tmp_path / "last_run.x.json")
    target = document["targets"][0]
    assert target["verdict"] == "NEEDS_CHANGES"
    assert target["score"] == 66.7
    assert "score_overall" not in target
    assert "scores" not in target


def test_cmd_review_quiet_json_yaml_and_exit(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    cache = home / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)

    captured = {}

    def fake_run(command, *, playbook_root, verbose, env=None):
        captured["command"] = command
        captured["verbose"] = verbose
        extras = json.loads(command[command.index("-e") + 1])
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
    code = cmd_review(_args(commit=True, playbook_root=str(checkout), as_json=True, verbose=True))
    out = capsys.readouterr()
    assert captured["verbose"] is False
    assert "-v" in captured["command"]
    payload = json.loads(out.out)
    assert payload["targets"][0]["verdict"] == "NEEDS_CHANGES"
    assert "TASK [noisy]" not in out.out
    assert "TASK [noisy]" in out.err
    assert code == 2

    capsys.readouterr()
    code = cmd_review(_args(commit=True, playbook_root=str(checkout), verbose=True))
    assert captured["verbose"] is True
    assert "-v" in captured["command"]
    assert "-vv" not in captured["command"]
    assert code == 2

    capsys.readouterr()
    code = cmd_review(_args(commit=True, playbook_root=str(checkout), debug=True))
    assert captured["verbose"] is True
    assert "-vv" in captured["command"]
    assert code == 2


def test_main_plai_and_plaibook_commit_match(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    (home / ".cache" / "ansible-plaibook").mkdir(parents=True)

    def fake_run(command, *, playbook_root, verbose, env=None):
        extras = json.loads(command[command.index("-e") + 1])
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

    def fake_run(command, *, playbook_root, verbose, env=None):
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


def test_format_elapsed_and_spinner_gate(monkeypatch):
    from io import StringIO

    from plaibook.wait import format_elapsed, spinner_enabled

    assert format_elapsed(0) == "0s"
    assert format_elapsed(7.9) == "7s"
    assert format_elapsed(75) == "1:15"

    quiet = StringIO()
    monkeypatch.delenv("PLAIBOOK_SPINNER", raising=False)
    assert spinner_enabled(quiet) is False

    class Tty(StringIO):
        def isatty(self) -> bool:
            return True

    tty = Tty()
    assert spinner_enabled(tty) is True
    monkeypatch.setenv("PLAIBOOK_SPINNER", "0")
    assert spinner_enabled(tty) is False


def test_spinner_rgb_walks_the_hue_circle():
    from plaibook.wait import hsv_to_rgb, spinner_rgb

    assert hsv_to_rgb(0.0) == (255, 0, 0)
    assert hsv_to_rgb(1.0 / 3.0) == (0, 255, 0)
    assert hsv_to_rgb(2.0 / 3.0) == (0, 0, 255)
    blue = spinner_rgb(0.0)
    later = spinner_rgb(1.5)
    assert blue == (0, 0, 255)
    assert later != blue


def test_wait_spinner_writes_frames_on_tty(monkeypatch):
    import time

    from plaibook.wait import WaitSpinner

    class Tty:
        def __init__(self) -> None:
            self.buf: list[str] = []

        def isatty(self) -> bool:
            return True

        def write(self, s: str) -> int:
            self.buf.append(s)
            return len(s)

        def flush(self) -> None:
            return None

    stream = Tty()
    monkeypatch.delenv("PLAIBOOK_SPINNER", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    with WaitSpinner("Reviewing commit HEAD in .", stream=stream):
        time.sleep(0.2)
    text = "".join(stream.buf)
    assert "Reviewing commit HEAD in ." in text
    assert any(ch in text for ch in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    assert "\n" in text
    assert "  setup" in text
    assert "\033[?25l" in text
    assert "\033[?25h" in text
    assert "38;2;" not in text


def test_wait_spinner_truecolor_when_color_enabled(monkeypatch):
    import time

    from plaibook.wait import WaitSpinner

    class Tty:
        def __init__(self) -> None:
            self.buf: list[str] = []

        def isatty(self) -> bool:
            return True

        def write(self, s: str) -> int:
            self.buf.append(s)
            return len(s)

        def flush(self) -> None:
            return None

    stream = Tty()
    monkeypatch.delenv("PLAIBOOK_SPINNER", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    with WaitSpinner("Reviewing org/repo#1", stream=stream):
        time.sleep(0.2)
    text = "".join(stream.buf)
    assert "38;2;" in text


def test_wait_spinner_reads_progress_file(tmp_path, monkeypatch):
    import time

    from plaibook.wait import WaitSpinner

    class Tty:
        def __init__(self) -> None:
            self.buf: list[str] = []

        def isatty(self) -> bool:
            return True

        def write(self, s: str) -> int:
            self.buf.append(s)
            return len(s)

        def flush(self) -> None:
            return None

    progress = tmp_path / "progress.txt"
    progress.write_text("lenses\n")
    stream = Tty()
    monkeypatch.delenv("PLAIBOOK_SPINNER", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    with WaitSpinner("Reviewing org/repo#1", stream=stream, progress_file=progress):
        time.sleep(0.2)
        progress.write_text("explore\n")
        time.sleep(0.2)
    text = "".join(stream.buf)
    assert "  lenses" in text
    assert "  explore" in text


def test_cmd_review_pr_fails_closed_without_openshell(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    called = []

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr("plaibook.cli.openshell_available", lambda: False)
    monkeypatch.setattr("plaibook.cli.running_inside_openshell", lambda: False)
    monkeypatch.setattr(
        "plaibook.cli.run_ansible_playbook",
        lambda *args, **kwargs: called.append(True),
    )

    code = cmd_review(_args(target="org/repo#1", playbook_root=str(checkout)))
    err = capsys.readouterr().err
    assert code == 2
    assert called == []
    assert "require a sandbox" in err


def test_load_vars_malformed_yaml_is_config_error(tmp_path):
    from plaibook.config import ConfigError, load_vars

    path = tmp_path / "vars.yml"
    path.write_text("agent_family: [unterminated\n", encoding="utf-8")
    try:
        load_vars(path=path)
    except ConfigError as exc:
        assert str(path) in str(exc)
        assert "cannot parse" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_load_vars_non_mapping_is_config_error(tmp_path):
    from plaibook.config import ConfigError, load_vars

    path = tmp_path / "vars.yml"
    path.write_text("", encoding="utf-8")
    assert load_vars(path=path) == {}
    path.write_text("[]\n", encoding="utf-8")
    try:
        load_vars(path=path)
    except ConfigError as exc:
        assert str(path) in str(exc)
        assert "mapping" in str(exc)
    else:
        raise AssertionError("expected ConfigError")
    path.write_text("agent_family: cursor\n", encoding="utf-8")
    assert load_vars(path=path)["agent_family"] == "cursor"


def test_cmd_review_non_mapping_vars_yml(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    xdg = tmp_path / "xdg"
    (xdg / "ansible-plaibook").mkdir(parents=True)
    (xdg / "ansible-plaibook" / "vars.yml").write_text("- not-a-mapping\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("ANSIBLE_REVIEW_AGENT_FAMILY", raising=False)
    monkeypatch.setattr("plaibook.cli.openshell_available", lambda: True)

    code = cmd_review(_args(commit=True, playbook_root=str(checkout)))
    err = capsys.readouterr().err
    assert code == 2
    assert "mapping" in err
    assert "vars.yml" in err


def test_cmd_review_malformed_vars_yml(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    xdg = tmp_path / "xdg"
    (xdg / "ansible-plaibook").mkdir(parents=True)
    (xdg / "ansible-plaibook" / "vars.yml").write_text("agent_family: [\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("ANSIBLE_REVIEW_AGENT_FAMILY", raising=False)
    monkeypatch.setattr("plaibook.cli.openshell_available", lambda: True)

    code = cmd_review(_args(commit=True, playbook_root=str(checkout)))
    err = capsys.readouterr().err
    assert code == 2
    assert "cannot parse" in err
    assert "vars.yml" in err


def test_playbook_timeout_seconds_rejects_non_positive(monkeypatch):
    from plaibook.playbook import playbook_timeout_seconds

    monkeypatch.delenv("PLAIBOOK_PLAYBOOK_TIMEOUT", raising=False)
    assert playbook_timeout_seconds() == 3600
    monkeypatch.setenv("PLAIBOOK_PLAYBOOK_TIMEOUT", "0")
    try:
        playbook_timeout_seconds()
    except ValueError as exc:
        assert "PLAIBOOK_PLAYBOOK_TIMEOUT" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_run_ansible_playbook_times_out(tmp_path, monkeypatch):
    from plaibook.playbook import PlaybookTimeoutError, run_ansible_playbook

    monkeypatch.setenv("PLAIBOOK_PLAYBOOK_TIMEOUT", "0.2")
    (tmp_path / "ansible.cfg").write_text("[defaults]\n")
    try:
        run_ansible_playbook(["sleep", "10"], playbook_root=tmp_path, verbose=False)
    except PlaybookTimeoutError as exc:
        assert exc.seconds == 0.2
        assert "timeout" in str(exc)
    else:
        raise AssertionError("expected PlaybookTimeoutError")


def test_extra_vars_rejects_last_run_id_override():
    try:
        extra_vars_from_args(
            _args(target="org/repo#1", cli_extra_vars=["last_run_id=attacker"]),
            "runId0123456789",
        )
    except ValueError as exc:
        assert "last_run_id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_running_inside_openshell_ignores_endpoint_only(tmp_path):
    from plaibook.config import running_inside_openshell

    missing = tmp_path / "no-jwt"
    assert (
        running_inside_openshell(
            env={"OPENSHELL_ENDPOINT": "https://gateway.example"},
            jwt_path=missing,
        )
        is False
    )
    assert (
        running_inside_openshell(
            env={"OPENSHELL_SANDBOX": "box-1", "OPENSHELL_SANDBOX_ID": "abc"},
            jwt_path=missing,
        )
        is False
    )
    jwt = tmp_path / "sandbox.jwt"
    jwt.write_text("x")
    assert running_inside_openshell(env={}, jwt_path=jwt) is True


def test_save_vars_oserror_is_config_error(tmp_path):
    from plaibook.config import ConfigError, save_vars

    blocker = tmp_path / "notdir"
    blocker.write_text("x", encoding="utf-8")
    try:
        save_vars({"agent_family": "cursor"}, path=blocker / "vars.yml")
    except ConfigError as exc:
        assert "cannot write" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_pretty_strips_ansi_from_findings_and_report():
    bell = "\x1b[31mRED\x1b[0m\rINJECT"
    pretty = format_pretty(
        {
            "targets": [
                {
                    "target": "org/repo#1",
                    "verdict": "NEEDS_CHANGES",
                    "score": 50,
                    "findings": [
                        {
                            "severity": "Major",
                            "file": "app.py",
                            "line": 1,
                            "title": bell,
                            "description": "why " + bell,
                        }
                    ],
                    "report": "report " + bell,
                }
            ]
        },
        full=True,
    )
    assert "\x1b" not in pretty
    assert "\r" not in pretty
    assert "INJECT" in pretty
    assert "RED" in pretty


def test_pretty_collapses_newlines_in_single_line_fields():
    pretty = format_pretty(
        {
            "status": "ok\nFAKE_STATUS",
            "last_run_path": "/tmp/last\nrun.json",
            "cost_usd": "1.5\nUSD",
            "targets": [
                {
                    "target": "org/repo#1\nFAKE_TARGET",
                    "verdict": "NEEDS_CHANGES\nFAKE_VERDICT",
                    "score": 50,
                    "scores": {"functionality": 90, "evil\nkey": 1},
                    "findings_path": "/tmp/findings.md\nFAKE_PATH",
                    "commit": "abc\ndef",
                    "cache_hit": True,
                    "findings": [
                        {
                            "severity": "Major",
                            "file": "app.py\n/etc/passwd",
                            "line": "1\n2",
                            "title": "title\nmore",
                            "description": "why\nline",
                        }
                    ],
                    "report": "report line 1\nreport line 2",
                }
            ],
        },
        full=True,
    )
    assert "\r" not in pretty
    assert "FAKE_TARGET" in pretty
    assert "FAKE_VERDICT" in pretty
    assert "FAKE_PATH" in pretty
    body_lines = [line for line in pretty.splitlines() if line]
    assert "FAKE_TARGET" not in body_lines
    assert "FAKE_VERDICT" not in body_lines
    assert "FAKE_PATH" not in body_lines
    assert "report line 1" in pretty
    assert "report line 2" in pretty
    header = next(line for line in pretty.splitlines() if "NEEDS_CHANGES" in line)
    assert "FAKE_VERDICT" in header
    assert "FAKE_TARGET" in header


def test_pretty_status_error_are_single_line():
    pretty = format_pretty({"status": "failed\nX", "error": "boom\nline2"})
    lines = [line for line in pretty.splitlines() if line]
    assert lines[0] == "status: failed X"
    assert lines[1] == "boom line2"


def test_load_json_corrupt_is_summary_error(tmp_path):
    from plaibook.summary import SummaryError, load_json

    path = tmp_path / "last_run.x.json"
    path.write_text("{truncated", encoding="utf-8")
    try:
        load_json(path)
    except SummaryError as exc:
        assert str(path) in str(exc)
        assert "cannot parse" in str(exc)
    else:
        raise AssertionError("expected SummaryError")
    path.write_text("[]", encoding="utf-8")
    try:
        load_json(path)
    except SummaryError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("expected SummaryError")


def test_cmd_review_corrupt_last_run(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "review.yml").write_text("---\n")
    (checkout / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    (home / ".cache" / "ansible-plaibook").mkdir(parents=True)

    def fake_run(command, *, playbook_root, verbose, env=None):
        extras = json.loads(command[command.index("-e") + 1])
        path = last_run_path(extras["last_run_id"], home=home)
        path.write_text("{truncated", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr("plaibook.cli.run_ansible_playbook", fake_run)
    monkeypatch.setattr(
        "plaibook.cli.build_ansible_command",
        lambda **kwargs: build_ansible_command(ansible_bin="ansible-playbook", **kwargs),
    )
    monkeypatch.setattr("plaibook.cli.last_run_path", lambda run_id: last_run_path(run_id, home=home))
    monkeypatch.setattr("plaibook.cli.resolve_family", lambda **kwargs: None)

    code = cmd_review(_args(commit=True, playbook_root=str(checkout)))
    err = capsys.readouterr().err
    assert code == 2
    assert "cannot parse" in err
    assert "Traceback" not in err
