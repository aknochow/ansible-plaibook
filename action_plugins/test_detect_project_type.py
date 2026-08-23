# -*- coding: utf-8 -*-
"""Behavioral tests for detect_project_type.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

Covers the type-detection rules, including two fixtures: a
python-project (pyproject.toml only) and a k8s-operator (go.mod
containing "sigs.k8s.io/controller-runtime" plus an empty PROJECT file).
"""
from __future__ import annotations

from detect_project_type import ActionModule, detect_project_type

_ALL_ABSENT = {
    "go.mod": False, "PROJECT": False, "galaxy.yml": False, "galaxy.yaml": False,
    "pyproject.toml": False, "setup.py": False, "setup.cfg": False, "review-checklist.md": False,
}


def test_python_project_baseline():
    file_exists = {**_ALL_ABSENT, "pyproject.toml": True}
    assert detect_project_type(file_exists, None) == "python-project"


def test_k8s_operator_baseline():
    file_exists = {**_ALL_ABSENT, "go.mod": True, "PROJECT": True}
    go_mod_content = "module example.com/operator\n\nrequire sigs.k8s.io/controller-runtime v0.15.0\n"
    assert detect_project_type(file_exists, go_mod_content) == "k8s-operator"


def test_k8s_operator_requires_both_go_mod_and_project_file():
    # go.mod alone (no PROJECT file) must fall through to go-project,
    # even if the go.mod content itself matches the k8s pattern.
    file_exists = {**_ALL_ABSENT, "go.mod": True}
    go_mod_content = "require sigs.k8s.io/controller-runtime v0.15.0\n"
    assert detect_project_type(file_exists, go_mod_content) == "go-project"


def test_k8s_operator_requires_content_match_not_just_both_files():
    # Both go.mod and PROJECT exist, but go.mod's content doesn't
    # mention controller-runtime -- falls through to go-project.
    file_exists = {**_ALL_ABSENT, "go.mod": True, "PROJECT": True}
    assert detect_project_type(file_exists, "module example.com/plain\n") == "go-project"


def test_ansible_collection_baseline():
    file_exists = {**_ALL_ABSENT, "galaxy.yml": True}
    assert detect_project_type(file_exists, None) == "ansible-collection"


def test_ansible_collection_yaml_extension_also_matches():
    file_exists = {**_ALL_ABSENT, "galaxy.yaml": True}
    assert detect_project_type(file_exists, None) == "ansible-collection"


def test_go_project_baseline():
    file_exists = {**_ALL_ABSENT, "go.mod": True}
    assert detect_project_type(file_exists, "module example.com/plain\n") == "go-project"


def test_unknown_when_nothing_matches():
    assert detect_project_type(_ALL_ABSENT, None) == "unknown"


def test_rule_order_k8s_operator_beats_go_project():
    # A repo that would match BOTH k8s-operator and go-project (it has
    # go.mod either way) must report k8s-operator -- first-match-wins
    # ordering, not "most specific" computed some other way.
    file_exists = {**_ALL_ABSENT, "go.mod": True, "PROJECT": True}
    go_mod_content = "sigs.k8s.io/controller-runtime\n"
    assert detect_project_type(file_exists, go_mod_content) == "k8s-operator"


def test_rule_order_ansible_collection_beats_python_project():
    file_exists = {**_ALL_ABSENT, "galaxy.yml": True, "pyproject.toml": True}
    assert detect_project_type(file_exists, None) == "ansible-collection"


def test_missing_go_mod_content_treated_as_not_k8s():
    # go.mod/PROJECT both reported as existing, but content wasn't
    # actually read (None) -- must not crash, must not match k8s.
    file_exists = {**_ALL_ABSENT, "go.mod": True, "PROJECT": True}
    assert detect_project_type(file_exists, None) == "go-project"


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "detect_project_type"
        self.async_val = False
        self.check_mode = False


def _run_action_module(file_exists, go_mod_content=None):
    action = ActionModule(
        task=_FakeTask({"file_exists": file_exists, "go_mod_content": go_mod_content}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    file_exists = {**_ALL_ABSENT, "pyproject.toml": True}
    result = _run_action_module(file_exists)
    assert "failed" not in result
    assert result["type"] == "python-project"


def test_action_module_requires_file_exists_arg():
    action = ActionModule(
        task=_FakeTask({}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
