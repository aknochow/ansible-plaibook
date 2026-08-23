# -*- coding: utf-8 -*-
"""Tests for scaffold_action_plugin.py.

Run separately from the action-plugin suite (different directory, not
part of the "pytest action_plugins/" command documented elsewhere --
this is a dev-only generator tool, not runtime pipeline code):
    /opt/homebrew/bin/python3.10 -m pytest scripts/

Checks the generator produces syntactically valid, structurally
correct scaffolds -- not that the (deliberately unimplemented,
NotImplementedError-raising) generated code does anything real yet.
"""
from __future__ import annotations

import py_compile

import pytest

import scaffold_action_plugin as scaffold


def test_generate_writes_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    plugin_path, test_path = scaffold.generate("compute_something", ["findings", "threshold"], "compute something")
    assert plugin_path == tmp_path / "compute_something.py"
    assert test_path == tmp_path / "test_compute_something.py"
    assert plugin_path.exists()
    assert test_path.exists()


def test_generated_plugin_file_is_valid_python(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    plugin_path, _ = scaffold.generate("compute_something", ["findings", "threshold"], "compute something")
    py_compile.compile(str(plugin_path), doraise=True)


def test_generated_test_file_is_valid_python(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    _, test_path = scaffold.generate("compute_something", ["findings", "threshold"], "compute something")
    py_compile.compile(str(test_path), doraise=True)


def test_generated_files_compile_with_a_single_argument_too(tmp_path, monkeypatch):
    # The single-arg case has its own message-formatting branch and a
    # trailing comma inside _VALID_ARGS's frozenset(("arg",)) -- worth
    # its own check, not just assumed to generalize from the two-arg case.
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    plugin_path, test_path = scaffold.generate("strip_something", ["findings"], "strip something")
    py_compile.compile(str(plugin_path), doraise=True)
    py_compile.compile(str(test_path), doraise=True)
    assert 'frozenset(("findings",))' in plugin_path.read_text()


def test_refuses_to_overwrite_existing_plugin_file(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    scaffold.generate("compute_something", ["findings"], "compute something")
    with pytest.raises(FileExistsError):
        scaffold.generate("compute_something", ["findings"], "compute something again")


def test_rejects_non_snake_case_plugin_name(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    with pytest.raises(ValueError):
        scaffold.generate("ComputeSomething", ["findings"], "d")
    with pytest.raises(ValueError):
        scaffold.generate("compute-something", ["findings"], "d")


def test_rejects_non_snake_case_arg_names(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    with pytest.raises(ValueError, match="arg names"):
        scaffold.generate("compute_something", ["findings", "Not-Valid"], "d")


def test_rejects_arg_names_that_would_break_generated_python():
    # Flagged in MR !17 review: unvalidated args interpolate directly
    # into generated source (arg extraction lines, _VALID_ARGS,
    # fake-task-args dict) -- confirm the exact adversarial-shaped
    # example from that finding is rejected, not just a generic bad name.
    with pytest.raises(ValueError):
        scaffold.generate("compute_something", ['x"); import os; os.system("rm -rf /") #'], "d")


def test_rejects_description_containing_triple_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    with pytest.raises(ValueError, match="description"):
        scaffold.generate("compute_something", ["findings"], 'ends the docstring early """ then keeps going')


def test_rejects_multiline_description(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    with pytest.raises(ValueError, match="description"):
        scaffold.generate("compute_something", ["findings"], "line one\nline two")


def test_main_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    exit_code = scaffold.main(["compute_something", "--args", "findings,threshold", "--description", "compute something"])
    assert exit_code == 0
    assert (tmp_path / "compute_something.py").exists()
    assert (tmp_path / "test_compute_something.py").exists()
    out = capsys.readouterr().out
    assert "Generated" in out


def test_main_cli_reports_error_on_existing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scaffold, "ACTION_PLUGINS_DIR", tmp_path)
    scaffold.main(["compute_something", "--args", "findings"])
    exit_code = scaffold.main(["compute_something", "--args", "findings"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err
