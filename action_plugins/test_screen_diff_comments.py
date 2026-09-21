# -*- coding: utf-8 -*-
"""Behavioral tests for screen_diff_comments.py.

Line-number preservation is a correctness invariant: blank comments in
place, never delete lines. A shift makes gold unmappable and posts
inline PR comments on the wrong lines.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase
from screen_diff_comments import (
    ActionModule,
    language_for_path,
    screen_unified_diff,
)

MULTI_LANG_DIFF = """\
diff --git a/plaibook/cli.py b/plaibook/cli.py
index abc1234..def5678 100644
--- a/plaibook/cli.py
+++ b/plaibook/cli.py
@@ -1,8 +1,12 @@
+\"\"\"CLI wrapper around review.yml.\"\"\"
 def cmd_review(args):
-    # old comment
+    # captured stdout is only dumped when last_run is absent
     x = "hash # not a comment"
+    y = 1  # inline
     return x
diff --git a/roles/review/defaults/main.yml b/roles/review/defaults/main.yml
index 1111111..2222222 100644
--- a/roles/review/defaults/main.yml
+++ b/roles/review/defaults/main.yml
@@ -1,3 +1,4 @@
+# env-lookup default, no baked-in choice
 review_type: pr
 url: "https://example.com/foo#section"
diff --git a/roles/review/templates/review_agent_prompt.j2 b/roles/review/templates/review_agent_prompt.j2
index 3333333..4444444 100644
--- a/roles/review/templates/review_agent_prompt.j2
+++ b/roles/review/templates/review_agent_prompt.j2
@@ -1,3 +1,5 @@
+{# this is intent the lens must not see #}
 You are a reviewer.
+Keep this.
diff --git a/README.md b/README.md
index 5555555..6666666 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,4 @@
 # Title
+<!-- hidden from lens -->
 Hello
"""


def _line_count(text: str) -> int:
    return len(text.splitlines(keepends=True))


def test_language_for_path():
    assert language_for_path("plaibook/cli.py") == "python+jinja"
    assert language_for_path("roles/review/defaults/main.yml") == "yaml+jinja"
    assert language_for_path("playbook.yaml") == "yaml+jinja"
    assert language_for_path("roles/review/templates/x.md.j2") == "markdown+jinja"
    assert language_for_path("roles/review/templates/foo.yml.j2") == "yaml+jinja"
    assert language_for_path("roles/review/templates/review_agent_prompt.j2") == "jinja"
    assert language_for_path("README.md") == "markdown+jinja"
    assert language_for_path("docs/note.markdown") == "markdown+jinja"
    assert language_for_path("uv.lock") == "unknown"


def test_multi_lang_diff_preserves_per_file_and_total_line_counts():
    result = screen_unified_diff(MULTI_LANG_DIFF, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert result["original_line_count"] == _line_count(MULTI_LANG_DIFF)
    assert result["screened_line_count"] == result["original_line_count"]
    assert result["screened_diff"].count("\n") == MULTI_LANG_DIFF.count("\n")
    by_path = {item["path"]: item for item in result["per_file"]}
    assert by_path["plaibook/cli.py"]["original_lines"] == by_path["plaibook/cli.py"]["screened_lines"]
    assert by_path["README.md"]["original_lines"] == by_path["README.md"]["screened_lines"]


def test_python_hash_comments_blanked_strings_kept_docstrings_kept():
    result = screen_unified_diff(MULTI_LANG_DIFF, screen_docstrings=False)
    screened = result["screened_diff"]
    assert "captured stdout" not in screened
    assert "old comment" not in screened
    assert 'x = "hash # not a comment"' in screened
    assert '"""CLI wrapper around review.yml."""' in screened
    assert "+    y = 1" in screened
    # inline comment text gone, line still present
    assert "inline" not in screened


def test_python_docstring_stripped_only_when_flag_on():
    kept = screen_unified_diff(MULTI_LANG_DIFF, screen_docstrings=False)
    stripped = screen_unified_diff(MULTI_LANG_DIFF, screen_docstrings=True)
    assert '"""CLI wrapper around review.yml."""' in kept["screened_diff"]
    assert "CLI wrapper around review.yml" not in stripped["screened_diff"]
    assert kept["original_line_count"] == stripped["screened_line_count"]
    # assignment strings are not docstrings
    assert 'x = "hash # not a comment"' in stripped["screened_diff"]


def test_yaml_hash_comments_blanked_but_not_inside_quotes():
    result = screen_unified_diff(MULTI_LANG_DIFF, screen_docstrings=False)
    screened = result["screened_diff"]
    assert "env-lookup default" not in screened
    assert 'url: "https://example.com/foo#section"' in screened


def test_jinja_comments_blanked_including_tag():
    result = screen_unified_diff(MULTI_LANG_DIFF, screen_docstrings=False)
    screened = result["screened_diff"]
    assert "intent the lens must not see" not in screened
    assert "You are a reviewer." in screened
    assert "+Keep this." in screened


def test_markdown_html_comments_blanked_heading_kept():
    result = screen_unified_diff(MULTI_LANG_DIFF, screen_docstrings=False)
    screened = result["screened_diff"]
    assert "hidden from lens" not in screened
    # Markdown ATX headings are not HTML comments
    assert "# Title" in screened
    assert "Hello" in screened


def test_hunk_header_ranges_kept_source_suffix_screened():
    diff = (
        "diff --git a/foo#bar.py b/foo#bar.py\n"
        "index abc#def..123#456 100644\n"
        "--- a/foo#bar.py\n"
        "+++ b/foo#bar.py\n"
        "@@ -1,1 +1,2 @@ def foo():  # hunk header must not reach the lens\n"
        " value = 1\n"
        "+# real comment\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "@@ -1,1 +1,2 @@" in result["screened_diff"]
    assert "hunk header must not reach the lens" not in result["screened_diff"]
    assert "diff --git a/foo#bar.py b/foo#bar.py" in result["screened_diff"]
    assert "index abc#def..123#456 100644" in result["screened_diff"]
    assert "real comment" not in result["screened_diff"]


def test_multiline_jinja_comment_preserves_blanked_lines():
    diff = (
        "diff --git a/roles/review/templates/x.j2 b/roles/review/templates/x.j2\n"
        "--- a/roles/review/templates/x.j2\n"
        "+++ b/roles/review/templates/x.j2\n"
        "@@ -1,1 +1,4 @@\n"
        "+{# line one\n"
        "+   line two\n"
        "+#}\n"
        "+keep\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert _line_count(result["screened_diff"]) == _line_count(diff)
    assert "line one" not in result["screened_diff"]
    assert "line two" not in result["screened_diff"]
    assert "+keep\n" in result["screened_diff"]


def test_never_deletes_lines_even_when_whole_hunk_is_comments():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+# a\n"
        "+# b\n"
        "+# c\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    plus_lines = [ln for ln in result["screened_diff"].splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert len(plus_lines) == 3
    assert all(ln == "+" + (" " * (len(ln) - 1)) or ln.startswith("+") for ln in plus_lines)
    assert "a" not in result["screened_diff"].split("@@", 1)[1]


def test_unknown_language_is_left_alone():
    diff = (
        "diff --git a/uv.lock b/uv.lock\n"
        "--- a/uv.lock\n"
        "+++ b/uv.lock\n"
        "@@ -1 +1,2 @@\n"
        " name = \"foo\"\n"
        "+# this hash is not a comment syntax we claim to know\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert "# this hash is not a comment syntax we claim to know" in result["screened_diff"]


def test_action_module_run_requires_diff_content(monkeypatch):
    plugin = ActionModule.__new__(ActionModule)

    class _Task:
        args = {}

    plugin._task = _Task()
    monkeypatch.setattr(ActionBase, "run", lambda self, tmp=None, task_vars=None: {})
    result = ActionModule.run(plugin, tmp=None, task_vars={})
    assert result["failed"] is True
    assert "diff_content" in result["msg"]


def test_action_module_run_ok_and_asserts_counts(monkeypatch):
    plugin = ActionModule.__new__(ActionModule)

    class _Task:
        args = {"diff_content": MULTI_LANG_DIFF, "screen_docstrings": False}

    plugin._task = _Task()
    monkeypatch.setattr(ActionBase, "run", lambda self, tmp=None, task_vars=None: {})
    result = ActionModule.run(plugin, tmp=None, task_vars={})
    assert result.get("failed") is not True
    assert result["line_counts_match"] is True
    assert result["screened_diff"].count("\n") == MULTI_LANG_DIFF.count("\n")


def test_assignment_triple_quote_is_not_a_docstring():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,1 @@\n"
        '+msg = """keep this contract"""\n'
    )
    stripped = screen_unified_diff(diff, screen_docstrings=True)
    assert "keep this contract" in stripped["screened_diff"]


def test_plus_plus_header_wins_when_path_contains_space_b_slash():
    diff = (
        "diff --git a/foo b/bar.py b/foo b/bar.py\n"
        "--- a/foo b/bar.py\n"
        "+++ b/foo b/bar.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+# inject\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["per_file"][0]["path"] == "foo b/bar.py"
    assert result["line_counts_match"] is True
    assert "inject" not in result["screened_diff"]


def test_jinja_comment_with_quote_does_not_poison_yaml_lexer():
    diff = (
        "diff --git a/roles/review/templates/x.yml.j2 b/roles/review/templates/x.yml.j2\n"
        "--- a/roles/review/templates/x.yml.j2\n"
        "+++ b/roles/review/templates/x.yml.j2\n"
        "@@ -0,0 +1,3 @@\n"
        '+{# " unmatched quote #}\n'
        "+keep: 1\n"
        "+# yaml comment after jinja\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "unmatched quote" not in result["screened_diff"]
    assert "yaml comment after jinja" not in result["screened_diff"]
    assert "+keep: 1\n" in result["screened_diff"]


def test_lexer_state_does_not_carry_across_hunk_headers():
    diff = (
        "diff --git a/roles/review/templates/x.j2 b/roles/review/templates/x.j2\n"
        "--- a/roles/review/templates/x.j2\n"
        "+++ b/roles/review/templates/x.j2\n"
        "@@ -1,1 +1,2 @@\n"
        "+{# unclosed in this hunk\n"
        " keep_hunk_1\n"
        "@@ -8,1 +8,2 @@\n"
        "+visible_code\n"
        " keep_hunk_2\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "unclosed in this hunk" not in result["screened_diff"]
    assert "+visible_code\n" in result["screened_diff"]


def test_jinja_comments_in_ansible_yaml_without_j2_suffix():
    diff = (
        "diff --git a/roles/review/tasks/main.yml b/roles/review/tasks/main.yml\n"
        "--- a/roles/review/tasks/main.yml\n"
        "+++ b/roles/review/tasks/main.yml\n"
        "@@ -0,0 +1,3 @@\n"
        "+{# lens must not see this in a playbook #}\n"
        "+- name: Keep this task\n"
        "+  debug: { msg: hello }\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "lens must not see this in a playbook" not in result["screened_diff"]
    assert "- name: Keep this task" in result["screened_diff"]
    assert "msg: hello" in result["screened_diff"]


def test_python_backslash_continued_string_keeps_hash():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,4 @@\n"
        '+s = "abc \\\n'
        "+# not a comment\n"
        '+def"\n'
        "+x = 1  # real comment\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "# not a comment" in result["screened_diff"]
    assert "real comment" not in result["screened_diff"]
    assert "+x = 1" in result["screened_diff"]


def test_jinja_comments_in_markdown_without_j2_suffix():
    diff = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -0,0 +1,3 @@\n"
        "+# Title\n"
        "+{# lens must not see this in markdown #}\n"
        "+Hello\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "lens must not see this in markdown" not in result["screened_diff"]
    assert "# Title" in result["screened_diff"]
    assert "Hello" in result["screened_diff"]


def test_yaml_block_scalar_hash_is_data_not_comment():
    diff = (
        "diff --git a/roles/review/tasks/main.yml b/roles/review/tasks/main.yml\n"
        "--- a/roles/review/tasks/main.yml\n"
        "+++ b/roles/review/tasks/main.yml\n"
        "@@ -0,0 +1,6 @@\n"
        "+script: |\n"
        "+  #!/bin/sh\n"
        "+  echo hi\n"
        "+# real yaml comment\n"
        "+other: 1\n"
        "+keep: visible\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "#!/bin/sh" in result["screened_diff"]
    assert "echo hi" in result["screened_diff"]
    assert "real yaml comment" not in result["screened_diff"]
    assert "keep: visible" in result["screened_diff"]


def test_yaml_block_scalar_indent_then_chomp_keeps_hash_data():
    """YAML allows `|2-` (indent indicator, then chomping), not only `|-2`."""
    diff = (
        "diff --git a/roles/review/tasks/main.yml b/roles/review/tasks/main.yml\n"
        "--- a/roles/review/tasks/main.yml\n"
        "+++ b/roles/review/tasks/main.yml\n"
        "@@ -0,0 +1,5 @@\n"
        "+script: |2-\n"
        "+  # hash_data_must_keep\n"
        "+  echo hi\n"
        "+# real_yaml_comment_must_blank\n"
        "+keep: visible\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "hash_data_must_keep" in result["screened_diff"]
    assert "echo hi" in result["screened_diff"]
    assert "real_yaml_comment_must_blank" not in result["screened_diff"]
    assert "keep: visible" in result["screened_diff"]


def test_jinja_comments_in_python_outside_strings_only():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+{# lens must not see this in python #}\n"
        '+kept = "{# keep inside string #}"\n'
        "+x = 1\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "lens must not see this in python" not in result["screened_diff"]
    assert '{# keep inside string #}' in result["screened_diff"]
    assert "+x = 1\n" in result["screened_diff"]


def test_continued_python_string_then_triple_quotes_does_not_bypass_hash_comment():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,4 @@\n"
        '+s = "abc \\\n'
        '+"""\n'
        "+# hash_comment_must_blank\n"
        '+end"\n'
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert '"""' in result["screened_diff"]
    assert "hash_comment_must_blank" not in result["screened_diff"]


def test_jinja_comment_containing_triple_quotes_does_not_desync_python():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,4 @@\n"
        '+{# """\n'
        "+still jinja\n"
        "+#}\n"
        "+# real python comment\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "still jinja" not in result["screened_diff"]
    assert "real python comment" not in result["screened_diff"]


def test_in_hunk_triple_plus_comment_is_screened_not_a_file_header():
    diff = (
        "diff --git a/play.yml b/play.yml\n"
        "--- a/play.yml\n"
        "+++ b/play.yml\n"
        "@@ -0,0 +1,2 @@\n"
        "+++ # hash_comment_must_blank\n"
        "+# other_comment_must_blank\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert result["per_file"][0]["path"] == "play.yml"
    assert "hash_comment_must_blank" not in result["screened_diff"]
    assert "other_comment_must_blank" not in result["screened_diff"]


def test_in_hunk_triple_plus_does_not_retarget_path():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+++ unknown.lock\n"
        "+# hash_comment_must_blank\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert result["per_file"][0]["path"] == "mod.py"
    assert "hash_comment_must_blank" not in result["screened_diff"]
    assert "+++ unknown.lock\n" in result["screened_diff"]


def test_in_hunk_triple_minus_comment_is_screened():
    diff = (
        "diff --git a/play.yml b/play.yml\n"
        "--- a/play.yml\n"
        "+++ b/play.yml\n"
        "@@ -1,2 +1,1 @@\n"
        "--- # removed_comment_must_blank\n"
        " keep: 1\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert result["per_file"][0]["path"] == "play.yml"
    assert "removed_comment_must_blank" not in result["screened_diff"]
    assert " keep: 1\n" in result["screened_diff"]


def test_deleted_quoted_path_with_spaces_screens_comments():
    diff = (
        'diff --git "a/old file.py" "b/old file.py"\n'
        "deleted file mode 100644\n"
        "index abc1234..0000000\n"
        '--- "a/old file.py"\n'
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-# deleted_comment_must_blank\n"
        "-x = 1\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert result["per_file"][0]["path"] == "old file.py"
    assert "deleted_comment_must_blank" not in result["screened_diff"]
    assert "-x = 1\n" in result["screened_diff"]


def test_quoted_plus_plus_path_with_spaces_screens_comments():
    diff = (
        'diff --git "a/old file.py" "b/old file.py"\n'
        '--- "a/old file.py"\n'
        '+++ "b/old file.py"\n'
        "@@ -0,0 +1,1 @@\n"
        "+# hash_comment_must_blank\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert result["per_file"][0]["path"] == "old file.py"
    assert "hash_comment_must_blank" not in result["screened_diff"]


def test_prefixed_docstring_stripped_when_flag_on():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,2 @@\n"
        '+r"""keep this contract"""\n'
        '+msg = r"""keep assignment"""\n'
    )
    kept = screen_unified_diff(diff, screen_docstrings=False)
    stripped = screen_unified_diff(diff, screen_docstrings=True)
    assert "keep this contract" in kept["screened_diff"]
    assert "keep this contract" not in stripped["screened_diff"]
    assert "keep assignment" in stripped["screened_diff"]


def test_context_line_blanked_when_old_stream_still_in_multiline_comment():
    diff = (
        "diff --git a/roles/review/templates/x.j2 b/roles/review/templates/x.j2\n"
        "--- a/roles/review/templates/x.j2\n"
        "+++ b/roles/review/templates/x.j2\n"
        "@@ -1,4 +1,2 @@\n"
        "-{#\n"
        " context_marker_must_blank\n"
        "-#}\n"
        " keep\n"
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "context_marker_must_blank" not in result["screened_diff"]
    assert " keep\n" in result["screened_diff"]


def test_escaped_triple_quote_does_not_close_python_string():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -0,0 +1,3 @@\n"
        '+s = """foo\\"""\n'
        "+# hash_in_string_must_keep\n"
        '+bar"""\n'
    )
    result = screen_unified_diff(diff, screen_docstrings=False)
    assert result["line_counts_match"] is True
    assert "hash_in_string_must_keep" in result["screened_diff"]
    assert 'bar"""' in result["screened_diff"]
