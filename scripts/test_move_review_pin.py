# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess

import move_review_pin as move
import pytest

OLD = "a" * 40
NEW = "b" * 40


def _tree(tmp_path):
    for name, text in (
        (move.RUN, f'ref: "{OLD}"\nref: "{OLD}"\n'),
        (move.CALLER, "uses: x/plai-review-run.yml@" + "c" * 40 + "\n"),
        ("docs/dependency-map.md", f"checkout {OLD}\n"),
        ("docs/github-review-check.md", "no pin here\n"),
        ("scripts/test_plai_review_workflow.py", "no pin here\n"),
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_moves_the_pin_everywhere_it_is_quoted(tmp_path):
    root = _tree(tmp_path)
    assert move.move_pin("checkout", NEW, root) == [move.RUN, "docs/dependency-map.md"]
    assert OLD not in (root / move.RUN).read_text(encoding="utf-8")
    assert NEW in (root / "docs/dependency-map.md").read_text(encoding="utf-8")


def test_refuses_a_short_sha(tmp_path):
    with pytest.raises(ValueError):
        move.move_pin("checkout", "abc123", _tree(tmp_path))


def test_refuses_mixed_pins(tmp_path):
    root = _tree(tmp_path)
    (root / move.RUN).write_text(f'ref: "{OLD}"\nref: "{NEW}"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        move.move_pin("checkout", NEW, root)


def test_the_real_tree_has_one_of_each_pin():
    assert move.current_pin("checkout")
    assert move.current_pin("caller")


def test_main_refuses_a_commit_not_on_main(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1 if "merge-base" in command else 0)

    monkeypatch.setattr(move.subprocess, "run", fake_run)
    monkeypatch.setattr(move, "move_pin", lambda *a: pytest.fail("moved"))
    assert move.main(["checkout", NEW]) == 1


def test_main_moves_a_commit_on_main(monkeypatch, capsys):
    monkeypatch.setattr(move.subprocess, "run", lambda command, **k: subprocess.CompletedProcess(command, 0))
    monkeypatch.setattr(move, "move_pin", lambda pin, commit: ["changed"])
    assert move.main(["checkout", NEW]) == 0
    assert "changed" in capsys.readouterr().out

