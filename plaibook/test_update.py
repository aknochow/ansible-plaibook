# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess

from plaibook.cli import build_parser, main
from plaibook.update import upgrade_plaibook


def test_update_parser_is_a_subcommand():
    parser = build_parser(prog="plai")
    args = parser.parse_args(["update"])
    assert args.command == "update"
    assert "plai update" in parser.format_help()


def test_update_runs_pipx_upgrade(monkeypatch):
    monkeypatch.setattr("plaibook.update.shutil.which", lambda name: "/usr/bin/pipx" if name == "pipx" else None)
    recorded = []

    def fake_run(argv, check):
        recorded.append((list(argv), check))
        return subprocess.CompletedProcess(argv, 0)

    assert upgrade_plaibook(run=fake_run) == 0
    assert recorded == [(["/usr/bin/pipx", "upgrade", "plaibook"], False)]


def test_update_returns_pipx_status(monkeypatch):
    monkeypatch.setattr("plaibook.update.shutil.which", lambda _name: "/usr/bin/pipx")

    def fake_run(argv, check):
        return subprocess.CompletedProcess(argv, 2)

    assert upgrade_plaibook(run=fake_run) == 2


def test_update_without_pipx_does_not_run(monkeypatch, capsys):
    monkeypatch.setattr("plaibook.update.shutil.which", lambda _name: None)

    def fake_run(argv, check):
        raise AssertionError("pipx must not run")

    assert upgrade_plaibook(run=fake_run) == 1
    assert "pipx is not on PATH" in capsys.readouterr().err


def test_main_update_dispatches(monkeypatch):
    monkeypatch.setattr("plaibook.cli.upgrade_plaibook", lambda: 0)
    assert main(["update"]) == 0
