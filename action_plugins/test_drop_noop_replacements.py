# -*- coding: utf-8 -*-
"""A finding whose replacement is the code it cites refutes itself."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from drop_noop_replacements import cited_lines, drop_noop_replacements, new_side_lines

# The plai-review bot's Minor on a real PR: "the new filter omits parameter
# and return type annotations", with a replacement identical to the line.
SIGNATURE = 'def openai_instruction_role(base_url: str, configured: str = "") -> str:'
DIFF = f"""diff --git a/filter_plugins/openai_conversation.py b/filter_plugins/openai_conversation.py
index 1111111..2222222 100644
--- a/filter_plugins/openai_conversation.py
+++ b/filter_plugins/openai_conversation.py
@@ -185,3 +191,5 @@ def openai_lens_reasoning_effort(model: str, configured: str = "") -> str:
         return ""


+{SIGNATURE}
+    \"\"\"Role for the instruction message of a Chat Completions call.
diff --git a/docs/reference.md b/docs/reference.md
--- a/docs/reference.md
+++ b/docs/reference.md
@@ -10,2 +10,3 @@
 | a | b |
-| old | row |
+++ looks like a header but is an added line
+| new | row |
"""


def _finding(**overrides):
    finding = {
        "file": "filter_plugins/openai_conversation.py",
        "line": 194,
        "severity": "Minor",
        "description": "The new filter omits parameter and return type annotations.",
        "replacement": SIGNATURE,
    }
    finding.update(overrides)
    return finding


def test_new_side_lines_follow_hunks_and_skip_removed_lines():
    files = new_side_lines(DIFF)
    code = files["filter_plugins/openai_conversation.py"]
    assert code[191] == '        return ""'
    assert code[192] == "" and code[193] == ""
    assert code[194] == SIGNATURE
    docs = files["docs/reference.md"]
    assert docs == {10: "| a | b |", 11: "++ looks like a header but is an added line", 12: "| new | row |"}


def test_the_real_minor_with_an_identical_replacement_is_dropped():
    kept, dropped, cleared = drop_noop_replacements([_finding()], DIFF)
    assert kept == [] and len(dropped) == 1 and cleared == 0


def test_surrounding_newlines_do_not_matter():
    kept, dropped, _ = drop_noop_replacements([_finding(severity="Nit", replacement=f"\n{SIGNATURE}\n")], DIFF)
    assert kept == [] and len(dropped) == 1


def test_removing_trailing_whitespace_is_a_real_change():
    diff = DIFF.replace(f"+{SIGNATURE}\n", f"+{SIGNATURE}   \n")
    finding = _finding(severity="Nit", description="Trailing whitespace.")
    kept, dropped, _ = drop_noop_replacements([finding], diff)
    assert kept == [finding] and dropped == []


@pytest.mark.parametrize(
    "header, path",
    [
        ("+++ b/docs/my notes.md\t", "docs/my notes.md"),  # git's form for a space
        ('+++ "b/docs/q\\"t.md"', 'docs/q"t.md'),  # git's form for a quote
        ('+++ "b/docs/caf\\303\\251.md"', "docs/caf\u00e9.md"),  # octal UTF-8
    ],
)
def test_git_path_headers_are_matched(header, path):
    diff = f"diff --git a/x b/x\n--- a/x\n{header}\n@@ -1 +1 @@\n-old\n+new\n"
    finding = _finding(file=path, line=1, replacement="new")
    assert drop_noop_replacements([finding], diff)[1] == [finding]


def test_a_real_change_is_kept():
    finding = _finding(replacement=SIGNATURE.replace("-> str:", "-> str | None:"))
    kept, dropped, cleared = drop_noop_replacements([finding], DIFF)
    assert kept == [finding] and dropped == [] and cleared == 0


def test_major_keeps_the_finding_and_loses_only_the_replacement():
    finding = _finding(severity="Major")
    kept, dropped, cleared = drop_noop_replacements([finding], DIFF)
    assert dropped == [] and cleared == 1
    assert kept == [dict(finding, replacement="")]
    assert finding["replacement"] == SIGNATURE


def test_multi_line_ranges_compare_every_line():
    lines = f'{SIGNATURE}\n    """Role for the instruction message of a Chat Completions call.'
    same = _finding(start_line=194, line=195, replacement=lines)
    assert drop_noop_replacements([same], DIFF)[1] == [same]
    shorter = _finding(start_line=194, line=195, replacement=SIGNATURE)
    assert drop_noop_replacements([shorter], DIFF)[0] == [shorter]


@pytest.mark.parametrize(
    "overrides",
    [
        {"line": 200},  # outside the diff
        {"file": "other.py"},
        {"replacement": ""},
        {"replacement": None},
        {"line": "x"},
        {"start_line": 196, "line": 194},
    ],
)
def test_nothing_to_compare_keeps_the_finding(overrides):
    finding = _finding(**overrides)
    kept, dropped, cleared = drop_noop_replacements([finding], DIFF)
    assert kept == [finding] and dropped == [] and cleared == 0


def test_cited_lines_needs_every_line_in_the_diff():
    files = new_side_lines(DIFF)
    assert cited_lines(_finding(start_line=194, line=196), files) is None
    assert cited_lines(_finding(), files) == [SIGNATURE]


def test_an_empty_diff_or_a_non_dict_finding_is_kept():
    assert drop_noop_replacements([_finding(), "x"], "") == ([_finding(), "x"], [], 0)
    with pytest.raises(TypeError):
        drop_noop_replacements("not a list", DIFF)


def test_merge_and_explore_run_it_before_scoring():
    tasks = Path(__file__).resolve().parents[1] / "roles" / "review" / "tasks"
    for name in ("merge.yml", "explore.yml"):
        text = (tasks / name).read_text(encoding="utf-8")
        assert yaml.safe_load(text)
        assert text.index("drop_noop_replacements:") < text.index("compute_review_scores:")
    merge = (tasks / "merge.yml").read_text(encoding="utf-8")
    assert 'combined_findings: "{{ noop_replacement_result.findings }}"' in merge
    explore = (tasks / "explore.yml").read_text(encoding="utf-8")
    assert "deduped_findings + explore_noop_replacement_result.findings" in explore
