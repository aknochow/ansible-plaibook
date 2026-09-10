# -*- coding: utf-8 -*-
"""Behavioral tests for bump_collection_pins.py.

Run with: python3 -m pytest scripts/test_bump_collection_pins.py
"""
from __future__ import annotations

from bump_collection_pins import (
    PinChange,
    apply_pin_changes,
    format_pr_body,
    main,
    parse_git_sha_pins,
    plan_changes,
)

FIXTURE = """\
---
# header comment
collections:
  - name: https://github.com/aknochow/ansible-openshell.git
    type: git
    version: main
  - name: https://github.com/aknochow/ansible-openai.git
    type: git
    # pin comment
    version: 0df6de7215c55aca40938a59fb700a18abe33dae
  - name: https://github.com/aknochow/ansible-cursor.git
    type: git
    version: 059daf017c0fcc406f35905b26688836255a925a
  - name: community.general
  - name: ansible.posix
"""


def test_parse_skips_branch_pins_and_galaxy_collections():
    pins = parse_git_sha_pins(FIXTURE)
    names = [pin.name for pin in pins]
    assert names == [
        "https://github.com/aknochow/ansible-openai.git",
        "https://github.com/aknochow/ansible-cursor.git",
    ]
    assert pins[0].repo == "ansible-openai"
    assert pins[1].current == "059daf017c0fcc406f35905b26688836255a925a"


def test_apply_preserves_comments_and_floating_main():
    pins = parse_git_sha_pins(FIXTURE)
    changes = [
        PinChange(
            name="https://github.com/aknochow/ansible-cursor.git",
            owner="aknochow",
            repo="ansible-cursor",
            current="059daf017c0fcc406f35905b26688836255a925a",
            latest="1d221ce62090a0f0da70b87478cfcfd22be5a40e",
        ),
        PinChange(
            name="https://github.com/aknochow/ansible-openai.git",
            owner="aknochow",
            repo="ansible-openai",
            current="0df6de7215c55aca40938a59fb700a18abe33dae",
            latest="0df6de7215c55aca40938a59fb700a18abe33dae",
        ),
    ]
    updated = apply_pin_changes(FIXTURE, changes, pins)
    assert "version: main" in updated
    assert "# pin comment" in updated
    assert "1d221ce62090a0f0da70b87478cfcfd22be5a40e" in updated
    assert "059daf017c0fcc406f35905b26688836255a925a" not in updated
    assert "0df6de7215c55aca40938a59fb700a18abe33dae" in updated
    assert "community.general" in updated


def test_plan_changes_uses_injected_fetcher():
    pins = parse_git_sha_pins(FIXTURE)

    def fake_fetch(owner: str, repo: str) -> str:
        return {
            ("aknochow", "ansible-openai"): "0df6de7215c55aca40938a59fb700a18abe33dae",
            ("aknochow", "ansible-cursor"): "1d221ce62090a0f0da70b87478cfcfd22be5a40e",
        }[(owner, repo)]

    changes = plan_changes(pins, sha_fetcher=fake_fetch)
    by_repo = {change.repo: change for change in changes}
    assert by_repo["ansible-openai"].stale is False
    assert by_repo["ansible-cursor"].stale is True
    assert by_repo["ansible-cursor"].latest.startswith("1d221ce")


def test_abbreviated_pin_matching_full_sha_is_current():
    pins = parse_git_sha_pins(
        "collections:\n"
        "  - name: https://github.com/aknochow/ansible-cursor.git\n"
        "    type: git\n"
        "    version: 1d221ce\n"
    )
    changes = plan_changes(
        pins,
        sha_fetcher=lambda owner, repo: "1d221ce62090a0f0da70b87478cfcfd22be5a40e",
    )
    assert changes[0].stale is False


def test_format_pr_body_includes_compare_url():
    body = format_pr_body(
        [
            PinChange(
                name="https://github.com/aknochow/ansible-cursor.git",
                owner="aknochow",
                repo="ansible-cursor",
                current="059daf017c0fcc406f35905b26688836255a925a",
                latest="1d221ce62090a0f0da70b87478cfcfd22be5a40e",
            )
        ]
    )
    assert "aknochow/ansible-cursor" in body
    assert "059daf017c0f" in body
    assert "1d221ce62090" in body
    assert "github.com/aknochow/ansible-cursor/compare/" in body


def test_main_write_and_check(tmp_path, monkeypatch):
    requirements = tmp_path / "collections-requirements.yml"
    requirements.write_text(FIXTURE)
    monkeypatch.setattr(
        "bump_collection_pins.fetch_default_branch_sha",
        lambda owner, repo: {
            ("aknochow", "ansible-openai"): "0df6de7215c55aca40938a59fb700a18abe33dae",
            ("aknochow", "ansible-cursor"): "1d221ce62090a0f0da70b87478cfcfd22be5a40e",
        }[(owner, repo)],
    )
    assert main(["--file", str(requirements), "--check"]) == 1
    assert main(["--file", str(requirements), "--write"]) == 0
    text = requirements.read_text()
    assert "1d221ce62090a0f0da70b87478cfcfd22be5a40e" in text
    assert main(["--file", str(requirements), "--check"]) == 0


def test_main_missing_file(tmp_path):
    missing = tmp_path / "nope.yml"
    assert main(["--file", str(missing)]) == 1
