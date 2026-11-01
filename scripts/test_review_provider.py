from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "filter_plugins"))

from review_provider import review_collection_ref, review_effort_label

ROOT = Path(__file__).resolve().parent.parent
_PINS = ROOT / "collections-requirements.yml"


def test_every_family_resolves_from_the_repo_pin_file():
    for family, name in (
        ("claude", "claude"),
        ("claude_cli", "claude"),
        ("cursor", "cursor"),
        ("gemini", "gemini"),
        ("openai", "openai"),
        ("xai", "xai"),
    ):
        ref = review_collection_ref(family, str(_PINS))
        prefix, _, sha = ref.partition("@")
        assert prefix == f"aknochow.{name}"
        assert len(sha) == 7


def test_unknown_family_has_no_collection():
    assert review_collection_ref("unknown", str(_PINS)) == ""


def test_missing_or_malformed_pin_file_is_empty(tmp_path):
    assert review_collection_ref("openai", str(tmp_path / "missing.yml")) == ""
    bad = tmp_path / "bad.yml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    assert review_collection_ref("openai", str(bad)) == ""
    broken = tmp_path / "broken.yml"
    broken.write_text("collections: [", encoding="utf-8")
    assert review_collection_ref("openai", str(broken)) == ""


def test_pin_for_another_collection_is_not_used(tmp_path):
    pins = tmp_path / "pins.yml"
    pins.write_text(
        "collections:\n  - name: https://github.com/aknochow/ansible-gemini.git\n    version: abcdef1234\n",
        encoding="utf-8",
    )
    assert review_collection_ref("openai", str(pins)) == ""


def test_unsafe_pin_text_is_not_shown(tmp_path):
    pins = tmp_path / "pins.yml"
    pins.write_text(
        'collections:\n  - name: https://github.com/aknochow/ansible-openai.git\n    version: "\\n<!--"\n',
        encoding="utf-8",
    )
    assert review_collection_ref("openai", str(pins)) == ""


def test_effort_label():
    assert review_effort_label("high", "high") == "high"
    assert review_effort_label("medium", "none") == "lens medium, tools none"
    assert review_effort_label("", "none") == "lens default, tools none"
    assert review_effort_label("", "") == ""
