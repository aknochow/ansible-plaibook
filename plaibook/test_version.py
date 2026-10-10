# -*- coding: utf-8 -*-
"""plai --version distinguishes a PyPI install from a git install."""

from __future__ import annotations

import pytest

from plaibook import __version__, version_text
from plaibook.cli import build_parser

SHA = "664b8e1d39acf7c6df6ed432fcd294dd8d318afa"


def _git(revision: str | None, commit: str = SHA) -> dict:
    vcs = {"vcs": "git", "commit_id": commit}
    if revision is not None:
        vcs["requested_revision"] = revision
    return {"url": "https://github.com/aknochow/ansible-plaibook.git", "vcs_info": vcs}


def test_pypi_install_is_the_plain_version():
    assert version_text(None) == f"plaibook {__version__}"
    assert version_text({"url": "https://files.pythonhosted.org/plaibook.whl"}) == f"plaibook {__version__}"


def test_main_install_names_the_branch_and_commit():
    assert version_text(_git("main")) == f"plaibook {__version__} (dev, main@{SHA[:12]})"


def test_commit_install_is_dev_with_the_short_sha():
    assert version_text(_git(SHA)) == f"plaibook {__version__} (dev, {SHA[:12]})"
    assert version_text(_git(None)) == f"plaibook {__version__} (dev, {SHA[:12]})"


def test_release_tag_omits_dev():
    assert version_text(_git(f"v{__version__}")) == f"plaibook {__version__} (v{__version__}@{SHA[:12]})"


def test_branch_install_keeps_the_ref():
    assert version_text(_git("feat/issue-57")) == f"plaibook {__version__} (dev, feat/issue-57@{SHA[:12]})"


def test_unsafe_revision_falls_back_to_the_sha():
    assert version_text(_git("main\n")) == f"plaibook {__version__} (dev, {SHA[:12]})"
    assert version_text(_git("a" * 81)) == f"plaibook {__version__} (dev, {SHA[:12]})"


def test_bad_commit_stays_on_the_package_version():
    assert version_text(_git("main", commit="not-a-sha")) == f"plaibook {__version__}"


def test_version_flag_prints_the_label(capsys):
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.strip() == version_text()
