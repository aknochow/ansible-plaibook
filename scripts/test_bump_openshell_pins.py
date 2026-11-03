# -*- coding: utf-8 -*-
from __future__ import annotations

import io

import bump_openshell_pins as bump
import pytest

TEXT = "# comment\nOPENSHELL_VERSION=v0.1.2\nSANDBOX_IMAGE=x@sha256:old\n"


def test_read_pins_skips_comments():
    assert bump.read_pins(TEXT) == {"OPENSHELL_VERSION": "v0.1.2", "SANDBOX_IMAGE": "x@sha256:old"}


def test_apply_pins_keeps_comments_and_order():
    out = bump.apply_pins(TEXT, {"OPENSHELL_VERSION": "v0.1.6", "SANDBOX_IMAGE": "x@sha256:new"})
    assert out == "# comment\nOPENSHELL_VERSION=v0.1.6\nSANDBOX_IMAGE=x@sha256:new\n"


def test_replace_values_updates_quoted_docs():
    replacements = bump.changed_values(
        {"OPENSHELL_VERSION": "v0.1.2", "SANDBOX_IMAGE": "same"},
        {"OPENSHELL_VERSION": "v0.1.6", "SANDBOX_IMAGE": "same"},
    )
    assert replacements == {"v0.1.2": "v0.1.6"}
    assert bump.replace_values("installs v0.1.2 here", replacements) == "installs v0.1.6 here"


def test_main_writes_nothing_when_current(monkeypatch, tmp_path):
    pins = tmp_path / "pins.env"
    pins.write_text(TEXT, encoding="utf-8")
    monkeypatch.setattr(bump, "PINS", pins)
    monkeypatch.setattr(bump, "DOCS", ())
    monkeypatch.setattr(bump, "latest_pins", lambda: bump.read_pins(TEXT))
    monkeypatch.setattr(bump, "latest_sdk", lambda: "0.1.6")
    monkeypatch.setattr(bump, "move_sdk", lambda new: None)
    assert bump.main(["--write"]) == 0
    assert pins.read_text(encoding="utf-8") == TEXT


def test_main_fails_without_writing_on_a_bad_answer(monkeypatch, tmp_path):
    pins = tmp_path / "pins.env"
    pins.write_text(TEXT, encoding="utf-8")
    monkeypatch.setattr(bump, "PINS", pins)

    def bad():
        raise ValueError("unexpected")

    monkeypatch.setattr(bump, "latest_pins", bad)
    assert bump.main(["--write"]) == 1
    assert pins.read_text(encoding="utf-8") == TEXT


def test_sdk_outside_the_gateway_line_is_refused(monkeypatch):
    class Response:
        def __enter__(self):
            return io.BytesIO(b'{"info": {"version": "0.2.0"}}')

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(bump, "_open", lambda url: Response())
    with pytest.raises(ValueError):
        bump.latest_sdk()


def test_move_sdk_does_nothing_when_current(monkeypatch, tmp_path):
    pins = tmp_path / "ee.in"
    pins.write_text("openshell==0.1.6\n", encoding="utf-8")
    monkeypatch.setattr(bump, "EE_IN", pins)
    monkeypatch.setattr(bump.subprocess, "run", lambda *a, **k: pytest.fail("compiled"))
    assert bump.move_sdk("0.1.6") is None
