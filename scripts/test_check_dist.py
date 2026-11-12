# -*- coding: utf-8 -*-
"""The dist check that runs before a release is tagged and before upload."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from check_dist import WHEEL_FILES, check_dist, main


def _dist(tmp_path: Path, wheel_files=WHEEL_FILES, sdist_files=("plaibook-0.1.34/review.yml",)) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / "plaibook-0.1.34-py3-none-any.whl", "w") as archive:
        for name in wheel_files:
            archive.writestr(name, "x")
    with tarfile.open(dist / "plaibook-0.1.34.tar.gz", "w:gz") as archive:
        for name in sdist_files:
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    return dist


def test_complete_dist_passes(tmp_path: Path, capsys):
    dist = _dist(tmp_path)
    assert check_dist(dist) == "plaibook-0.1.34-py3-none-any.whl"
    assert main([str(dist)]) == 0
    assert "vendored playbook ok" in capsys.readouterr().out


def test_wheel_without_the_review_skill_fails(tmp_path: Path, capsys):
    dist = _dist(tmp_path, wheel_files=WHEEL_FILES[:-1])
    assert main([str(dist)]) == 1
    assert "skills/ansible-plaibook-review/SKILL.md" in capsys.readouterr().err


def test_sdist_without_review_yml_fails(tmp_path: Path, capsys):
    dist = _dist(tmp_path, sdist_files=("plaibook-0.1.34/README.md",))
    assert main([str(dist)]) == 1
    assert "missing review.yml" in capsys.readouterr().err


def test_missing_or_extra_archives_fail(tmp_path: Path, capsys):
    assert main([str(tmp_path / "nope")]) == 1
    dist = _dist(tmp_path)
    (dist / "plaibook-0.1.33-py3-none-any.whl").write_bytes(b"")
    assert main([str(dist)]) == 1
    assert "expected one wheel and one sdist" in capsys.readouterr().err
