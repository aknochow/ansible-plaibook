# -*- coding: utf-8 -*-
"""One-process briefing git snapshot matches git and the checkout reader."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[1] / "roles" / "review" / "files" / "briefing_snapshot.py"
READER = Path(__file__).resolve().parents[1] / "roles" / "review" / "files" / "read_checkout_files.py"
MARKERS = [
    "go.mod",
    "PROJECT",
    "galaxy.yml",
    "galaxy.yaml",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "review-checklist.md",
]


def _git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
        }
    )
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env=env,
    )
    return completed.stdout.decode()


def _init_repo(tmp_path: Path) -> tuple[str, str]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "old.txt").write_text("gone\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD").strip()
    (tmp_path / "old.txt").unlink()
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('kept')\n", encoding="utf-8")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "skip.py").write_text("SECRET_SKILL_BODY\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "change")
    head = _git(tmp_path, "rev-parse", "HEAD").strip()
    return base, head


def _snapshot(repo: Path, target: str, source: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SNAPSHOT), str(repo), target, source],
        input=json.dumps({"markers": MARKERS}),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def test_snapshot_matches_git_and_skips_skill_bodies(tmp_path: Path):
    base, head = _init_repo(tmp_path)
    result = _snapshot(tmp_path, base, head)
    assert result["diff"] == _git(tmp_path, "diff", base, head, "--")
    assert result["name_only"] == _git(tmp_path, "diff", "--name-only", base, head, "--").splitlines()
    assert result["numstat"] == _git(tmp_path, "diff", "--numstat", base, head, "--")
    assert result["deleted"] == _git(tmp_path, "diff", "--diff-filter=D", "--name-only", base, head, "--").splitlines()
    assert result["deleted"] == ["old.txt"]
    assert "skills/skip.py" in result["name_only"]
    assert "skills/skip.py" not in result["files"]
    assert result["files"]["src/app.py"] == "print('kept')\n"
    assert "SECRET_SKILL_BODY" not in json.dumps(result["files"])
    assert result["markers"]["pyproject.toml"] is True
    assert result["markers"]["go.mod"] is False
    assert result["go_mod"] is None
    assert result["commit_count"]["rc"] == 0
    assert result["commit_count"]["stdout"].strip() == "2"

    filtered = [path for path in result["name_only"] if "skills/" not in path and "evals/" not in path]
    reader = subprocess.run(
        [sys.executable, str(READER), str(tmp_path)],
        input=json.dumps({"markers": MARKERS, "files": filtered}),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    read = json.loads(reader.stdout)
    assert result["markers"] == read["markers"]
    assert result["go_mod"] == read["go_mod"]
    assert result["files"] == read["files"]


def test_rejects_a_ref_that_looks_like_a_git_option(tmp_path: Path):
    completed = subprocess.run(
        [sys.executable, str(SNAPSHOT), str(tmp_path), "--output", "HEAD"],
        input="{}",
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode != 0
    assert "does not start with '-'" in completed.stderr
