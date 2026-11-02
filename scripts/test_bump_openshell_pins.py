# -*- coding: utf-8 -*-
from __future__ import annotations

import bump_openshell_pins as bump

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
