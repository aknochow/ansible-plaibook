# -*- coding: utf-8 -*-
"""Behavioral tests for check_neutralization_references.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

This check has no shipped default pattern list (see the module's own
docstring), so every test here supplies org_reference_patterns
explicitly, standing in for whatever an operator would configure for
their own org. The fixture covers a diff that leaves one pattern match
in a non-comment line and excludes matches inside `#`/`//` comments.
"""
from __future__ import annotations

from check_neutralization_references import ActionModule, check_neutralization_references

PLACEHOLDER_PATTERNS = ["git.internal.example", "internal.example/certs", "internal-tooling-name", "org/internal-project"]

BASELINE_DIFF_CONTENT = (
    "diff --git a/README.md b/README.md\n"
    "index dab306f..0a539a9 100644\n"
    "--- a/README.md\n"
    "+++ b/README.md\n"
    "@@ -1 +1,3 @@\n"
    " # Project\n"
    "+\n"
    "+Neutralized hardcoded references for the internal-project rollout.\n"
    "diff --git a/config/settings.py b/config/settings.py\n"
    "index 8e57a90..713f252 100644\n"
    "--- a/config/settings.py\n"
    "+++ b/config/settings.py\n"
    "@@ -1 +1,4 @@\n"
    "+# config-driven settings, org-neutral by default\n"
    " GATEWAY_URL = \"https://internal.example.com/api\"\n"
    "+# TODO: this still points at git.internal.example internally\n"
    "+FALLBACK_URL = \"https://git.internal.example/some/repo\"\n"
    "diff --git a/script.sh b/script.sh\n"
    "index af7c9ad..ca42e8d 100644\n"
    "--- a/script.sh\n"
    "+++ b/script.sh\n"
    "@@ -1,2 +1,3 @@\n"
    " #!/bin/bash\n"
    "-echo \"hello\"\n"
    "+// this comment mentions internal.example/certs but should be skipped (comment line)\n"
    "+echo \"internal-tooling-name is now config-driven\"\n"
)
BASELINE_CHANGED_FILES = ["README.md", "config/settings.py", "script.sh"]
BASELINE_FILE_CONTENTS = {
    "README.md": "# Project\n\nNeutralized hardcoded references for the internal-project rollout.\n",
    "config/settings.py": (
        "# config-driven settings, org-neutral by default\n"
        "GATEWAY_URL = \"https://internal.example.com/api\"\n"
        "# TODO: this still points at git.internal.example internally\n"
        "FALLBACK_URL = \"https://git.internal.example/some/repo\"\n"
    ),
    "script.sh": (
        "#!/bin/bash\n"
        "// this comment mentions internal.example/certs but should be skipped (comment line)\n"
        "echo \"internal-tooling-name is now config-driven\"\n"
    ),
}
BASELINE_RESULT = [
    "config/settings.py:4: FALLBACK_URL = \"https://git.internal.example/some/repo\"",
    "script.sh:3: echo \"internal-tooling-name is now config-driven\"",
]


def test_matches_real_harness_baseline():
    result = check_neutralization_references(BASELINE_DIFF_CONTENT, BASELINE_CHANGED_FILES, BASELINE_FILE_CONTENTS, PLACEHOLDER_PATTERNS)
    assert result == BASELINE_RESULT


def test_no_patterns_configured_means_check_never_fires():
    # No shipped default -- an operator who hasn't configured
    # org_reference_patterns gets a silent no-op, not an accidental
    # scan against someone else's org strings.
    result = check_neutralization_references(BASELINE_DIFF_CONTENT, BASELINE_CHANGED_FILES, BASELINE_FILE_CONTENTS)
    assert result is None


def test_markdown_files_are_excluded_even_with_a_matching_line():
    # README.md's own content mentions "internal-project" but .md files
    # are always excluded, regardless of content.
    diff_files = ["README.md"]
    file_contents = {"README.md": "See org/internal-project for details.\n"}
    result = check_neutralization_references("this diff is config-driven", diff_files, file_contents, PLACEHOLDER_PATTERNS)
    assert result is None


def test_comment_lines_are_skipped_hash_and_slashslash():
    diff_files = ["a.py", "b.js"]
    file_contents = {
        "a.py": "# git.internal.example should be ignored here\n",
        "b.js": "// internal-tooling-name should be ignored here\n",
    }
    result = check_neutralization_references("hardcoded refs", diff_files, file_contents, PLACEHOLDER_PATTERNS)
    assert result is None


def test_triggered_but_no_remaining_refs_folds_to_none():
    # Matches the original's own `results if results else None` -- an
    # empty findings list after a triggered scan is indistinguishable
    # from "never triggered," by design, not by omission.
    diff_files = ["a.py"]
    file_contents = {"a.py": "nothing suspicious here\n"}
    result = check_neutralization_references("org-neutral cleanup", diff_files, file_contents, PLACEHOLDER_PATTERNS)
    assert result is None


def test_not_triggered_when_diff_mentions_no_signal_keyword():
    diff_files = ["a.py"]
    file_contents = {"a.py": "FALLBACK_URL = \"https://git.internal.example/some/repo\"\n"}
    result = check_neutralization_references("just an unrelated tweak", diff_files, file_contents, PLACEHOLDER_PATTERNS)
    assert result is None


def test_file_missing_from_file_contents_is_skipped_not_crashed():
    # Mirrors the original's own `if not ok: continue` for a failed git
    # show (e.g. a deleted file) -- here, a file simply absent from the
    # pre-fetched dict (never read, oversized, deleted, non-regular).
    diff_files = ["deleted.py"]
    result = check_neutralization_references("hardcoded refs", diff_files, {}, PLACEHOLDER_PATTERNS)
    assert result is None


def test_all_configured_patterns_are_detected():
    diff_files = ["a.py"]
    file_contents = {"a.py": "\n".join(f"line mentioning {p}" for p in PLACEHOLDER_PATTERNS) + "\n"}
    result = check_neutralization_references("hardcoded refs", diff_files, file_contents, PLACEHOLDER_PATTERNS)
    assert len(result) == 4


def test_result_lines_are_truncated_at_100_chars():
    diff_files = ["a.py"]
    long_line = "git.internal.example " + ("x" * 200)
    file_contents = {"a.py": long_line + "\n"}
    result = check_neutralization_references("hardcoded refs", diff_files, file_contents, PLACEHOLDER_PATTERNS)
    # "a.py:1: " prefix + first 100 chars of the stripped line
    assert result[0] == f"a.py:1: {long_line[:100]}"


# --- ActionModule wiring smoke test (see filter_self_refuted_findings's
# test file for why these are hand-rolled, narrow test doubles) --------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "check_neutralization_references"
        self.async_val = False
        self.check_mode = False


def _run_action_module(diff_content, changed_files, file_contents, org_reference_patterns=None):
    action = ActionModule(
        task=_FakeTask({
            "diff_content": diff_content,
            "changed_files": changed_files,
            "file_contents": file_contents,
            "org_reference_patterns": org_reference_patterns,
        }),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(BASELINE_DIFF_CONTENT, BASELINE_CHANGED_FILES, BASELINE_FILE_CONTENTS, PLACEHOLDER_PATTERNS)
    assert "failed" not in result
    assert result["triggered"] is True
    assert result["remaining_refs"] == BASELINE_RESULT


def test_action_module_normalizes_none_to_triggered_false_and_empty_list():
    result = _run_action_module("unrelated tweak", ["a.py"], {"a.py": "nothing here\n"}, PLACEHOLDER_PATTERNS)
    assert "failed" not in result
    assert result["triggered"] is False
    assert result["remaining_refs"] == []


def test_action_module_defaults_missing_org_reference_patterns_to_empty():
    result = _run_action_module(BASELINE_DIFF_CONTENT, BASELINE_CHANGED_FILES, BASELINE_FILE_CONTENTS)
    assert "failed" not in result
    assert result["triggered"] is False
    assert result["remaining_refs"] == []


def test_action_module_requires_all_args():
    action = ActionModule(
        task=_FakeTask({"diff_content": "", "changed_files": []}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
