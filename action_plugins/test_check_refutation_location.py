# -*- coding: utf-8 -*-
"""Behavioral tests for check_refutation_location.py."""
from __future__ import annotations

from check_refutation_location import _fragments, check_refutation_location

# Two mappings that share most keys, like write_last_run_files.yml had:
# the first includes 'collection', the second (cited) does not.
_PAD = ["# filler line that is long enough to count"] * 40
_FIRST = [
    "    content:",
    "        'agent_family': _last_run_agent_family,",
    "        'collection': _last_run_agent_family | review_collection_ref(path),",
    "        'effort': review_run_effort | default(''),",
]
_SECOND = [
    "    line:",
    "        'agent_family': _last_run_agent_family,",
    "        'effort': review_run_effort | default(''),",
    "        'models': pipeline_agent_calls | map(attribute='model') | list,",
]
CONTENT = "\n".join(["---", *_FIRST, *_PAD, *_SECOND, ""])
FIRST_AT = 3  # 'agent_family' line of the first block
SECOND_AT = 1 + len(_FIRST) + len(_PAD) + 2  # 'agent_family' line of the cited block

SIBLING_QUOTE = (
    "\"        'agent_family': _last_run_agent_family,\n"
    "        'collection': _last_run_agent_family | review_collection_ref(path),\n"
    "        'effort': review_run_effort | default(''),\""
)


def test_fixture_positions():
    lines = CONTENT.splitlines()
    assert "agent_family" in lines[FIRST_AT - 1]
    assert "agent_family" in lines[SECOND_AT - 1]


def test_quote_from_sibling_block_is_downgraded():
    out = check_refutation_location("refuted", SECOND_AT + 1, SIBLING_QUOTE, CONTENT)
    assert out["status"] == "inconclusive"
    assert out["downgraded"] is True
    assert f"line {FIRST_AT + 1}" in out["note"]
    assert f"cited line {SECOND_AT + 1}" in out["note"]


def test_quote_from_the_cited_block_is_kept():
    quote = "'agent_family': _last_run_agent_family,\n'models': pipeline_agent_calls | map(attribute='model') | list,"
    out = check_refutation_location("refuted", SECOND_AT + 1, quote, CONTENT)
    assert out == {"status": "refuted", "downgraded": False, "note": ""}


def test_quote_of_unrelated_far_code_is_kept():
    # A definition elsewhere that shares no line with the cited block.
    quote = "'collection': _last_run_agent_family | review_collection_ref(path),"
    out = check_refutation_location("refuted", SECOND_AT + 1, quote, CONTENT)
    assert out["status"] == "refuted"


def test_quote_from_another_file_is_kept():
    quote = "agent_family: \"{{ lookup('ansible.builtin.env', 'ANSIBLE_REVIEW_AGENT_FAMILY') }}\""
    assert check_refutation_location("refuted", SECOND_AT, quote, CONTENT)["status"] == "refuted"


def test_only_duplicated_lines_quoted_is_kept():
    # No line that pins down which block was read.
    quote = "'agent_family': _last_run_agent_family,\n'effort': review_run_effort | default(''),"
    assert check_refutation_location("refuted", SECOND_AT, quote, CONTENT)["status"] == "refuted"


def test_cited_block_far_from_both_copies_is_kept():
    # Cited line in the padding: neither copy is near it, so no sibling.
    out = check_refutation_location("refuted", FIRST_AT + 20, SIBLING_QUOTE, CONTENT)
    assert out["status"] == "refuted"


def test_other_statuses_and_missing_inputs_pass_through():
    for status in ("verified", "inconclusive", ""):
        assert check_refutation_location(status, SECOND_AT, SIBLING_QUOTE, CONTENT)["status"] == status
    assert check_refutation_location("refuted", SECOND_AT, SIBLING_QUOTE, "")["status"] == "refuted"
    assert check_refutation_location("refuted", 0, SIBLING_QUOTE, CONTENT)["status"] == "refuted"


def test_fragments_read_backtick_spans_and_drop_short_ones():
    text = "`roles/x.yml` says: `agent_family: default_value_here`\n`}`"
    frags = _fragments(text)
    assert "agent_family: default_value_here" in frags
    assert "}" not in frags


def test_cited_line_plus_unrelated_far_line_is_kept():
    # The quote takes the repeated line at the cited block and a unique
    # line from far away, but the repeated line's other copy is not near
    # that unique line: no far block, so the refutation stands.
    content = "\n".join([
        "        'agent_family': _last_run_agent_family,",
        *(["# filler line that is long enough to count"] * 30),
        "def helper_defined_far_away(value):",
        *(["# filler line that is long enough to count"] * 30),
        "        'agent_family': _last_run_agent_family,",
        "",
    ])
    quote = "'agent_family': _last_run_agent_family,\ndef helper_defined_far_away(value):"
    assert check_refutation_location("refuted", 63, quote, content)["status"] == "refuted"
