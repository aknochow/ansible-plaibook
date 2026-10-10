# -*- coding: utf-8 -*-
"""Behavioral tests for bump_collection_pins.py.

Run with: python3 -m pytest scripts/test_bump_collection_pins.py
"""

from __future__ import annotations

from pathlib import Path

from bump_collection_pins import (
    PinChange,
    apply_pin_changes,
    format_pr_body,
    main,
    parse_git_sha_pins,
    plan_changes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

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
  - name: https://github.com/ansible-collections/community.general.git
    type: git
    version: 049524674b13ad9782849c427266935c8ec61954
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


def test_parse_skips_third_party_release_shas():
    pins = parse_git_sha_pins(FIXTURE)
    assert all(pin.owner == "aknochow" for pin in pins)
    assert not any("ansible-collections" in pin.name for pin in pins)


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
    assert "049524674b13ad9782849c427266935c8ec61954" in updated


def test_plan_changes_uses_injected_fetcher():
    pins = parse_git_sha_pins(FIXTURE)

    def fake_fetch(owner: str, repo: str) -> str:
        return {
            ("aknochow", "ansible-openai"): "0df6de7215c55aca40938a59fb700a18abe33dae",
            ("aknochow", "ansible-cursor"): "1d221ce62090a0f0da70b87478cfcfd22be5a40e",
        }[(owner, repo)]

    changes = plan_changes(
        pins,
        sha_fetcher=fake_fetch,
        compare_fetcher=lambda owner, repo, current, latest: "ahead",
    )
    by_repo = {change.repo: change for change in changes}
    assert by_repo["ansible-openai"].stale is False
    assert by_repo["ansible-cursor"].stale is True
    assert by_repo["ansible-cursor"].latest.startswith("1d221ce")


def test_abbreviated_pin_matching_full_sha_is_current():
    pins = parse_git_sha_pins(
        "collections:\n  - name: https://github.com/aknochow/ansible-cursor.git\n    type: git\n    version: 1d221ce\n"
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
    monkeypatch.setattr(
        "bump_collection_pins.compare_pin_to_head",
        lambda owner, repo, current, latest: "ahead",
    )
    assert main(["--file", str(requirements), "--check"]) == 1
    assert main(["--file", str(requirements), "--write"]) == 0
    text = requirements.read_text()
    assert "1d221ce62090a0f0da70b87478cfcfd22be5a40e" in text
    assert main(["--file", str(requirements), "--check"]) == 0


def test_main_missing_file(tmp_path):
    missing = tmp_path / "nope.yml"
    assert main(["--file", str(missing)]) == 1


OPEN_SHELL_PIN = "742002497fc5320ad149bf10837a5a6c4948a5a3"
OPEN_SHELL_MAIN = "6314f0f7df7eb22557489fcff0087dd398031a41"
DIVERGED_REQUIREMENTS = f"""\
collections:
  - name: https://github.com/aknochow/ansible-openshell.git
    type: git
    version: {OPEN_SHELL_PIN}
"""


def _mock_github(monkeypatch, *, compare_status: str, head: str = OPEN_SHELL_MAIN):
    """Stub the GitHub REST helpers. urlopen must not run."""

    def fake_fetch_json(url, *, opener=None):
        if url.endswith("/repos/aknochow/ansible-openshell"):
            return {"default_branch": "main"}
        if url.endswith("/commits/main"):
            return {"sha": head}
        if "/compare/" in url:
            assert OPEN_SHELL_PIN in url
            assert head in url
            return {"status": compare_status}
        raise AssertionError(url)

    def refuse_network(*args, **kwargs):
        raise AssertionError("network call")

    monkeypatch.setattr("bump_collection_pins.fetch_json", fake_fetch_json)
    monkeypatch.setattr("bump_collection_pins.urllib.request.urlopen", refuse_network)


def test_diverged_pin_is_not_moved(tmp_path, monkeypatch):
    requirements = tmp_path / "collections-requirements.yml"
    body = tmp_path / "body.md"
    requirements.write_text(DIVERGED_REQUIREMENTS)
    _mock_github(monkeypatch, compare_status="diverged")
    assert main(["--file", str(requirements), "--check"]) == 0
    assert main(["--file", str(requirements), "--write", "--pr-body", str(body)]) == 0
    text = requirements.read_text()
    assert OPEN_SHELL_PIN in text
    assert OPEN_SHELL_MAIN not in text
    written = body.read_text()
    assert "diverged" in written
    assert "No pins moved." in written
    assert f"compare/{OPEN_SHELL_PIN}...{OPEN_SHELL_MAIN}" in written


def test_behind_pin_is_not_moved(tmp_path, monkeypatch):
    requirements = tmp_path / "collections-requirements.yml"
    body = tmp_path / "body.md"
    requirements.write_text(DIVERGED_REQUIREMENTS)
    _mock_github(monkeypatch, compare_status="behind")
    assert main(["--file", str(requirements), "--write", "--pr-body", str(body)]) == 0
    assert OPEN_SHELL_MAIN not in requirements.read_text()
    written = body.read_text()
    assert "behind" in written
    assert "No pins moved." in written


def test_fast_forward_pin_is_moved(tmp_path, monkeypatch):
    requirements = tmp_path / "collections-requirements.yml"
    body = tmp_path / "body.md"
    requirements.write_text(DIVERGED_REQUIREMENTS)
    _mock_github(monkeypatch, compare_status="ahead")
    assert main(["--file", str(requirements), "--check"]) == 1
    assert main(["--file", str(requirements), "--write", "--pr-body", str(body)]) == 0
    text = requirements.read_text()
    assert OPEN_SHELL_MAIN in text
    assert OPEN_SHELL_PIN not in text
    written = body.read_text()
    assert f"compare/{OPEN_SHELL_PIN}...{OPEN_SHELL_MAIN}" in written
    assert "Left in place" not in written


def test_identical_pin_does_not_call_compare_or_rewrite(tmp_path, monkeypatch):
    requirements = tmp_path / "collections-requirements.yml"
    body = tmp_path / "body.md"
    requirements.write_text(DIVERGED_REQUIREMENTS.replace(OPEN_SHELL_PIN, OPEN_SHELL_MAIN))

    def fake_fetch_json(url, *, opener=None):
        if "/compare/" in url:
            raise AssertionError(url)
        if url.endswith("/repos/aknochow/ansible-openshell"):
            return {"default_branch": "main"}
        if url.endswith("/commits/main"):
            return {"sha": OPEN_SHELL_MAIN}
        raise AssertionError(url)

    monkeypatch.setattr("bump_collection_pins.fetch_json", fake_fetch_json)
    monkeypatch.setattr(
        "bump_collection_pins.urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network call")),
    )
    before = requirements.read_text()
    assert main(["--file", str(requirements), "--check", "--write", "--pr-body", str(body)]) == 0
    assert requirements.read_text() == before
    assert "No pins moved." in body.read_text()


def test_repo_requirements_only_bumps_aknochow_interface_pins():
    pins = parse_git_sha_pins((REPO_ROOT / "collections-requirements.yml").read_text())
    assert pins
    assert {pin.owner for pin in pins} == {"aknochow"}
    repos = {pin.repo for pin in pins}
    assert repos == {
        "ansible-openshell",
        "ansible-claude",
        "ansible-gemini",
        "ansible-openai",
        "ansible-cursor",
    }
