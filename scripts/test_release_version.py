# -*- coding: utf-8 -*-
"""Version selection for the release bump and cut workflows."""

from __future__ import annotations

from pathlib import Path

import pytest
from release_cut import _UNSET, cut_release, release_payload, release_tag
from release_cut import main as cut_main
from release_version import main as version_main
from release_version import next_version, read_version, write_version

SHA = "a" * 40


def _tree(tmp_path: Path, version: str = "0.1.26") -> Path:
    (tmp_path / "plaibook").mkdir()
    (tmp_path / ".agents" / "skills" / "ansible-plaibook-review").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    (tmp_path / "plaibook" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (tmp_path / ".agents" / "skills" / "ansible-plaibook-review" / "SKILL.md").write_text(
        f'---\nversion: "{version}"\n---\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        f"(`:{version}` is the image for tag `v{version}`).\n"
        f"cosign verify ghcr.io/aknochow/plaibook-ee:{version}\n"
        f"plai update --branch v{version}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_patch_minor_and_major_from_the_current_package():
    assert next_version("0.1.26", "patch") == "0.1.27"
    assert next_version("0.1.26", "minor") == "0.2.0"
    assert next_version("0.1.26", "major") == "1.0.0"


def test_explicit_version_must_be_newer_x_y_z():
    assert next_version("0.1.26", "1.4.0") == "1.4.0"
    with pytest.raises(ValueError, match="newer"):
        next_version("0.1.26", "0.1.26")
    with pytest.raises(ValueError, match="X.Y.Z"):
        next_version("0.1.26", "v0.1.27")


def test_write_version_updates_the_four_release_files_only(tmp_path: Path):
    root = _tree(tmp_path)
    write_version(root, "0.1.27")
    assert read_version(root) == "0.1.27"
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "ghcr.io/aknochow/plaibook-ee:0.1.27" in readme
    assert "`:0.1.27` is the image for tag `v0.1.27`" in readme
    assert "plai update --branch v0.1.26" in readme


def test_print_current_does_not_require_a_bump(tmp_path: Path, capsys):
    root = _tree(tmp_path)
    assert version_main(["--print-current", "--root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == "0.1.26"
    assert read_version(root) == "0.1.26"


def test_write_without_bump_is_an_error(tmp_path: Path):
    root = _tree(tmp_path)
    with pytest.raises(SystemExit):
        version_main(["--root", str(root)])
    assert read_version(root) == "0.1.26"


def test_release_payload_is_not_a_prerelease():
    payload = release_payload("0.1.27", SHA)
    assert release_tag("0.1.27") == "v0.1.27"
    assert payload["tag_name"] == "v0.1.27"
    assert payload["target_commitish"] == SHA
    assert payload["draft"] is False
    assert payload["prerelease"] is False


def test_cut_release_refuses_an_existing_tag():
    def fetch(method, url, token, payload):
        assert token == "ghs_example"
        assert "GITHUB_TOKEN" not in url
        if method == "GET":
            return 200, {"ref": "refs/tags/v0.1.27"}
        raise AssertionError("must not create a release when the tag exists")

    with pytest.raises(RuntimeError, match="tag v0.1.27 already exists"):
        cut_release("acme/repo", "0.1.27", SHA, "ghs_example", fetch)


def test_cut_release_creates_a_release_when_the_tag_is_absent():
    calls = []

    def fetch(method, url, token, payload):
        calls.append((method, url, token, payload))
        if method == "GET":
            return 404, {"message": "Not Found"}
        return 201, {"html_url": "https://github.com/acme/repo/releases/tag/v0.1.27"}

    url = cut_release("acme/repo", "0.1.27", SHA, "ghs_example", fetch)
    assert url.endswith("/releases/tag/v0.1.27")
    assert calls[1][0] == "POST"
    assert calls[1][3]["prerelease"] is False
    assert calls[1][3]["target_commitish"] == SHA


def test_cut_fails_when_the_app_secret_is_unset(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.delenv("RELEASE_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("RELEASE_GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_default")
    root = _tree(tmp_path)
    assert cut_main(["--repo", "acme/repo", "--root", str(root), "--sha", SHA]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == _UNSET
    assert "ghs_default" not in captured.err
    assert "BEGIN" not in captured.err
