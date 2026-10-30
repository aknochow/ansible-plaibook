"""Behavioral tests for omit_generated_diff_files.py."""

import sys
from pathlib import Path

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
