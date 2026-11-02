# -*- coding: utf-8 -*-
"""The review uses the openshell CLI's recorded gateway endpoint."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _filter():
    path = Path(__file__).resolve().parents[1] / "filter_plugins" / "openshell_gateway.py"
    spec = importlib.util.spec_from_file_location("openshell_gateway", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_configured_gateway_endpoint_replaces_the_fallback(tmp_path):
    mod = _filter()
    gateway = tmp_path / ".config" / "openshell" / "gateways" / "openshell"
    gateway.mkdir(parents=True)
    (gateway / "metadata.json").write_text(
        json.dumps({"name": "openshell", "gateway_endpoint": "https://localhost:17670"}),
        encoding="utf-8",
    )
    assert mod.openshell_gateway_endpoint(
        "openshell",
        "https://host.openshell.internal:17670",
        config_home=str(tmp_path),
    ) == "https://localhost:17670"


def test_missing_config_keeps_the_fallback(tmp_path):
    mod = _filter()
    fallback = "https://host.openshell.internal:17670"
    assert mod.openshell_gateway_endpoint("openshell", fallback, config_home=str(tmp_path)) == fallback


def test_blank_name_defaults_to_openshell(tmp_path):
    mod = _filter()
    gateway = tmp_path / ".config" / "openshell" / "gateways" / "openshell"
    gateway.mkdir(parents=True)
    (gateway / "metadata.json").write_text(
        json.dumps({"gateway_endpoint": "https://localhost:17670"}),
        encoding="utf-8",
    )
    assert mod.openshell_gateway_endpoint("", "https://fallback.example", config_home=str(tmp_path)) == (
        "https://localhost:17670"
    )


def test_unsafe_endpoint_keeps_the_fallback(tmp_path):
    mod = _filter()
    gateway = tmp_path / ".config" / "openshell" / "gateways" / "openshell"
    gateway.mkdir(parents=True)
    (gateway / "metadata.json").write_text(
        json.dumps({"gateway_endpoint": "http://localhost:17670"}),
        encoding="utf-8",
    )
    fallback = "https://host.openshell.internal:17670"
    assert mod.openshell_gateway_endpoint("openshell", fallback, config_home=str(tmp_path)) == fallback


def test_gateway_name_cannot_escape_the_config_dir(tmp_path):
    mod = _filter()
    fallback = "https://host.openshell.internal:17670"
    assert mod.openshell_gateway_endpoint("../openshell", fallback, config_home=str(tmp_path)) == fallback


def test_missing_proxy_uses_the_installed_collection_script(tmp_path):
    mod = _filter()
    installed = (
        tmp_path
        / ".cache"
        / "ansible-plaibook"
        / "collections"
        / "ansible_collections"
        / "aknochow"
        / "openshell"
        / "scripts"
    )
    installed.mkdir(parents=True)
    script = installed / "ssh_proxy.py"
    script.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    missing = str(tmp_path / "code" / "ansible-openshell" / "scripts" / "ssh_proxy.py")
    assert mod.openshell_ssh_proxy(missing, home=str(tmp_path)) == str(script)


def test_existing_proxy_path_is_kept(tmp_path):
    mod = _filter()
    chosen = tmp_path / "ssh_proxy.py"
    chosen.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    assert mod.openshell_ssh_proxy(str(chosen), home=str(tmp_path)) == str(chosen)


def _installed_scripts(home):
    cache = home / ".cache" / "ansible-plaibook" / "collections"
    return cache / "ansible_collections" / "aknochow" / "openshell" / "scripts"


def test_unset_proxy_uses_the_installed_collection_script(tmp_path):
    mod = _filter()
    installed = _installed_scripts(tmp_path)
    installed.mkdir(parents=True)
    script = installed / "ssh_proxy.py"
    script.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    assert mod.openshell_ssh_proxy("", home=str(tmp_path)) == str(script)


def test_developer_checkout_is_not_used_unless_configured(tmp_path):
    mod = _filter()
    checkout = tmp_path / "code" / "ansible-openshell" / "scripts"
    checkout.mkdir(parents=True)
    (checkout / "ssh_proxy.py").write_text("stale\n", encoding="utf-8")
    installed = _installed_scripts(tmp_path)
    installed.mkdir(parents=True)
    script = installed / "ssh_proxy.py"
    script.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    assert mod.openshell_ssh_proxy("", home=str(tmp_path)) == str(script)


def test_collection_search_path_is_used_when_plaibook_cache_is_absent(tmp_path):
    mod = _filter()
    ee = tmp_path / "usr" / "share" / "ansible" / "collections"
    scripts = ee / "ansible_collections" / "aknochow" / "openshell" / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "ssh_proxy.py"
    script.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    assert mod.openshell_ssh_proxy("", home=str(home), collection_paths=[str(ee)]) == str(script)
    assert mod.openshell_ssh_proxy("", home=str(home), collection_paths=str(ee)) == str(script)


def test_no_proxy_found_returns_the_configured_value(tmp_path):
    mod = _filter()
    assert mod.openshell_ssh_proxy("", home=str(tmp_path), collection_paths=[]) == ""
