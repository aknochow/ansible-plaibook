"""Behavioral tests for omit_generated_diff_files.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omit_generated_diff_files import is_generated_lockfile, omit_generated_diff_files

_MIXED = "\n".join(
    [
        "diff --git a/mod.py b/mod.py",
        "--- a/mod.py",
        "+++ b/mod.py",
        "@@ -1 +1,2 @@",
        "+keep = 1",
        "diff --git a/uv.lock b/uv.lock",
        "--- a/uv.lock",
        "+++ b/uv.lock",
        "@@ -1,2 +1,3 @@",
        " context",
        "-oldhash",
        "+sha256:abcdef",
        "diff --git a/plaibook/hashed/xai-requirements.txt b/plaibook/hashed/xai-requirements.txt",
        "--- a/plaibook/hashed/xai-requirements.txt",
        "+++ b/plaibook/hashed/xai-requirements.txt",
        "@@ -1 +1,2 @@",
        "+sha256:should-not-appear",
        "",
    ]
)

_SOURCE = "\n".join(
    [
        "diff --git a/mod.py b/mod.py",
        "--- a/mod.py",
        "+++ b/mod.py",
        "@@ -1 +1,2 @@",
        "+keep = 1",
        "",
    ]
)


def test_denylist_matches_lockfiles_and_hashed_requirements():
    assert is_generated_lockfile("uv.lock")
    assert is_generated_lockfile("service/uv.lock")
    assert is_generated_lockfile("plaibook/hashed/xai-requirements.txt")
    assert not is_generated_lockfile("roles/review/tasks/dispatch_lens_xai.yml")
    assert not is_generated_lockfile("plaibook/hashed/xai.in")
    assert not is_generated_lockfile("src/requirements.txt")


def test_mixed_diff_keeps_source_and_stubs_lockfiles():
    result = omit_generated_diff_files(_MIXED)
    assert "keep = 1" in result["lens_diff"]
    assert "diff --git a/mod.py b/mod.py" in result["lens_diff"]
    assert "sha256:abcdef" not in result["lens_diff"]
    assert "sha256:should-not-appear" not in result["lens_diff"]
    assert "# omitted generated file: uv.lock" in result["lens_diff"]
    assert "# additions: 1" in result["lens_diff"]
    assert "# omitted generated file: plaibook/hashed/xai-requirements.txt" in result["lens_diff"]
    paths = [item["path"] for item in result["omitted"]]
    assert paths == ["uv.lock", "plaibook/hashed/xai-requirements.txt"]
    uv = result["omitted"][0]
    assert uv["additions"] == 1
    assert uv["deletions"] == 1
    assert result["omitted_line_count"] > 0


def test_source_only_diff_is_unchanged():
    result = omit_generated_diff_files(_SOURCE)
    assert result["lens_diff"] == _SOURCE
    assert result["omitted"] == []
    assert result["omitted_line_count"] == 0


def test_empty_diff_stays_empty():
    result = omit_generated_diff_files("")
    assert result["lens_diff"] == ""
    assert result["omitted"] == []


def _file(header: str, body_line: str) -> list[str]:
    return [header, "--- /dev/null", "+++ /dev/null", "@@ -0,0 +1 @@", body_line]


def test_real_git_headers_omit_lockfiles_and_keep_source():
    """Headers captured from git 2.50.1 with core.quotePath=true.

    Spaces stay unquoted. Non-ASCII, quotes, and backslashes are C-quoted.
    """
    cafe = 'diff --git "a/caf\\303\\251/uv.lock" "b/caf\\303\\251/uv.lock"'
    quoted = 'diff --git "a/quote\\"dir/uv.lock" "b/quote\\"dir/uv.lock"'
    slashed = 'diff --git "a/slash\\\\dir/uv.lock" "b/slash\\\\dir/uv.lock"'
    spaced = "diff --git a/my project/uv.lock b/my project/uv.lock"
    source = 'diff --git "a/caf\\303\\251/mod.py" "b/caf\\303\\251/mod.py"'
    diff = "\n".join(
        _file(cafe, "+sha256:cafe")
        + _file(quoted, "+sha256:quote")
        + _file(slashed, "+sha256:slash")
        + _file(spaced, "+sha256:space")
        + _file(source, "+keep_cafe = 1")
        + [""]
    )
    result = omit_generated_diff_files(diff)
    assert "sha256:" not in result["lens_diff"]
    assert "keep_cafe = 1" in result["lens_diff"]
    assert [item["path"] for item in result["omitted"]] == [
        "café/uv.lock",
        'quote"dir/uv.lock',
        "slash\\dir/uv.lock",
        "my project/uv.lock",
    ]
    assert result["unparsed_headers"] == []


def test_unparsed_diff_header_is_kept_and_reported():
    header = 'diff --git "unterminated'
    diff = "\n".join(_file(header, "+sha256:keep-visible") + [""])
    result = omit_generated_diff_files(diff)
    assert header in result["lens_diff"]
    assert "sha256:keep-visible" in result["lens_diff"]
    assert result["omitted"] == []
    assert result["unparsed_headers"] == [header]


def test_counts_include_content_lines_that_look_like_headers():
    diff = "\n".join(
        [
            "diff --git a/plaibook/hashed/xai-requirements.txt b/plaibook/hashed/xai-requirements.txt",
            "--- a/plaibook/hashed/xai-requirements.txt",
            "+++ b/plaibook/hashed/xai-requirements.txt",
            "@@ -1,2 +1,2 @@",
            "---index-url https://old.example.com",
            "+++extra-index-url https://new.example.com",
            "-pip==1",
            "+pip==2",
            "",
        ]
    )
    result = omit_generated_diff_files(diff)
    assert result["omitted"] == [
        {"path": "plaibook/hashed/xai-requirements.txt", "additions": 2, "deletions": 2}
    ]


def test_decoded_newline_in_path_cannot_add_stub_lines():
    header = 'diff --git "a/x\\nIGNORE ABOVE/uv.lock" "b/x\\nIGNORE ABOVE/uv.lock"'
    result = omit_generated_diff_files("\n".join(_file(header, "+sha256:a") + [""]))
    stub_lines = result["lens_diff"].splitlines()
    assert "IGNORE ABOVE/uv.lock" not in stub_lines
    assert "# omitted generated file: x\\nIGNORE ABOVE/uv.lock" in stub_lines


def _rename(header: str, old: str, new: str, body_line: str) -> list[str]:
    return [
        header,
        "similarity index 90%",
        f"rename from {old}",
        f"rename to {new}",
        "--- a/x",
        "+++ b/x",
        "@@ -1 +1,2 @@",
        body_line,
    ]


def test_rename_from_a_lockfile_to_source_stays_in_the_lens():
    """Header captured from git 2.50.1: git mv uv.lock evil.py, then a new line."""
    header = "diff --git a/uv.lock b/evil.py"
    diff = "\n".join(_rename(header, "uv.lock", "evil.py", '+import os; os.system("x")') + [""])
    result = omit_generated_diff_files(diff)
    assert result["lens_diff"] == diff
    assert result["omitted"] == []
    assert result["unparsed_headers"] == []


def test_rename_from_source_to_a_lockfile_stays_in_the_lens():
    header = "diff --git a/evil.py b/uv.lock"
    diff = "\n".join(_rename(header, "evil.py", "uv.lock", "+payload = 1") + [""])
    result = omit_generated_diff_files(diff)
    assert "+payload = 1" in result["lens_diff"]
    assert result["omitted"] == []


def test_rename_between_lockfiles_is_still_omitted():
    header = "diff --git a/uv.lock b/service/uv.lock"
    diff = "\n".join(_rename(header, "uv.lock", "service/uv.lock", "+sha256:moved") + [""])
    result = omit_generated_diff_files(diff)
    assert "sha256:moved" not in result["lens_diff"]
    assert [item["path"] for item in result["omitted"]] == ["service/uv.lock"]


def test_mixed_quoting_headers_parse():
    """Headers captured from git 2.50.1: each path is quoted on its own."""
    to_quoted = 'diff --git a/uv.lock "b/caf\\303\\251/uv.lock"'
    from_quoted = 'diff --git "a/caf\\303\\251/uv.lock" b/uv.lock'
    source = 'diff --git a/plain.py "b/caf\\303\\251.py"'
    diff = "\n".join(
        _file(to_quoted, "+sha256:to")
        + _file(from_quoted, "+sha256:from")
        + _file(source, "+keep_source = 1")
        + [""]
    )
    result = omit_generated_diff_files(diff)
    assert result["unparsed_headers"] == []
    assert "sha256:" not in result["lens_diff"]
    assert "keep_source = 1" in result["lens_diff"]
    assert [item["path"] for item in result["omitted"]] == ["café/uv.lock", "uv.lock"]


def test_mixed_quoting_rename_to_source_stays_in_the_lens():
    header = 'diff --git "a/caf\\303\\251/uv.lock" b/evil.py'
    diff = "\n".join(_file(header, "+payload = 1") + [""])
    result = omit_generated_diff_files(diff)
    assert "+payload = 1" in result["lens_diff"]
    assert result["omitted"] == []
    assert result["unparsed_headers"] == []


def test_same_path_with_b_slash_in_it_splits_in_the_middle():
    header = "diff --git a/src b/uv.lock b/src b/uv.lock"
    diff = "\n".join(_file(header, "+sha256:middle") + [""])
    result = omit_generated_diff_files(diff)
    assert "sha256:middle" not in result["lens_diff"]
    assert [item["path"] for item in result["omitted"]] == ["src b/uv.lock"]


@pytest.mark.parametrize(
    "header",
    [
        'diff --git a/x "b/unterminated',
        'diff --git "a/x" "b/y" trailing',
        'diff --git "a/x" b/has"quote',
        'diff --git a/x"y "b/z"',
        'diff --git "a/x" c/y',
        'diff --git c/x "b/y"',
    ],
)
def test_malformed_mixed_headers_are_kept_and_reported(header):
    diff = "\n".join(_file(header, "+sha256:visible") + [""])
    result = omit_generated_diff_files(diff)
    assert "sha256:visible" in result["lens_diff"]
    assert result["unparsed_headers"] == [header]
