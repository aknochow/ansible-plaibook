# -*- coding: utf-8 -*-
"""Behavioral tests for detect_diff_domains.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

Fixture covers a diff touching pyproject.toml,
roles/myrole/tasks/main.yml, script.py (containing a `subprocess.run(...)`
call), and .gitlab-ci.yml.
"""
from __future__ import annotations

from detect_diff_domains import ActionModule, detect_diff_domains

BASELINE_DIFF_FILES = [
    "pyproject.toml",
    "roles/myrole/tasks/main.yml",
    "script.py",
    ".gitlab-ci.yml",
]
BASELINE_FILE_CONTENTS = {
    "pyproject.toml": '[project]\nname = "test"\n',
    "roles/myrole/tasks/main.yml": "- name: do a thing\n  ansible.builtin.debug:\n    msg: hi\n",
    "script.py": "import subprocess\n\ndef run():\n    subprocess.run([\"ls\"])\n",
    ".gitlab-ci.yml": "stages: [test]\n",
}
BASELINE_DOMAINS = ["ansible", "ci-pipeline", "new-repo", "python-code", "python-packaging", "subprocess"]


def test_matches_real_harness_baseline():
    result = detect_diff_domains(BASELINE_DIFF_FILES, BASELINE_FILE_CONTENTS, is_new_repo=True)
    assert result == BASELINE_DOMAINS


def test_is_new_repo_false_omits_new_repo_domain():
    result = detect_diff_domains(BASELINE_DIFF_FILES, BASELINE_FILE_CONTENTS, is_new_repo=False)
    assert "new-repo" not in result
    assert result == sorted(d for d in BASELINE_DOMAINS if d != "new-repo")


def test_path_based_rule_needs_no_file_content():
    # ci-pipeline matches on the file PATH itself (.gitlab-ci.yml) --
    # must fire even with an empty file_contents dict.
    result = detect_diff_domains([".gitlab-ci.yml"], {}, is_new_repo=False)
    assert result == ["ci-pipeline"]


def test_content_match_rule_requires_content_present():
    # subprocess is a content_match rule -- a file path alone (no entry
    # in file_contents) must not trigger it.
    result = detect_diff_domains(["script.py"], {}, is_new_repo=False)
    assert "subprocess" not in result


def test_content_match_rule_fires_when_content_present():
    result = detect_diff_domains(["script.py"], {"script.py": "import subprocess\nsubprocess.run([])\n"}, is_new_repo=False)
    assert "subprocess" in result


def test_skill_and_evals_paths_are_excluded_from_every_rule():
    # _is_skill_definition excludes any path with a "skills" or "evals"
    # path component from ALL rules, path-based and content-based alike.
    diff_files = ["skills/code-review/scripts/detect_project.py", "evals/fixture.py"]
    file_contents = {f: "import subprocess\n" for f in diff_files}
    result = detect_diff_domains(diff_files, file_contents, is_new_repo=False)
    assert result == []


def test_basename_match_rule_matches_on_path_suffix():
    # api-endpoints uses basename_match: True -- "/routes.py" must match
    # as a path suffix (prefixed with "/"), not merely as a substring
    # search of the bare filename.
    result = detect_diff_domains(["app/routes.py"], {}, is_new_repo=False)
    assert "api-endpoints" in result


def test_database_domain_has_both_path_and_content_variants():
    # "database" appears twice in the pattern table -- once path-based
    # (models.py, migrations/, etc.), once content-based (sqlalchemy,
    # django.db, etc.). Either alone must produce the domain.
    path_result = detect_diff_domains(["app/models.py"], {}, is_new_repo=False)
    assert "database" in path_result

    content_result = detect_diff_domains(
        ["app/db.py"], {"app/db.py": "from sqlalchemy import create_engine\n"}, is_new_repo=False
    )
    assert "database" in content_result


def test_empty_diff_files_returns_no_domains():
    assert detect_diff_domains([], {}, is_new_repo=False) == []


def test_result_is_sorted():
    result = detect_diff_domains(BASELINE_DIFF_FILES, BASELINE_FILE_CONTENTS, is_new_repo=True)
    assert result == sorted(result)


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "detect_diff_domains"
        self.async_val = False
        self.check_mode = False


def _run_action_module(diff_files, file_contents, is_new_repo):
    action = ActionModule(
        task=_FakeTask({"diff_files": diff_files, "file_contents": file_contents, "is_new_repo": is_new_repo}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(BASELINE_DIFF_FILES, BASELINE_FILE_CONTENTS, True)
    assert "failed" not in result
    assert result["domains"] == BASELINE_DOMAINS


def test_action_module_requires_all_args():
    action = ActionModule(
        task=_FakeTask({"diff_files": [], "file_contents": {}}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True


def test_action_module_accepts_is_new_repo_false_without_flagging_it_missing():
    result = _run_action_module([], {}, False)
    assert "failed" not in result
