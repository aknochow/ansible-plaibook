# -*- coding: utf-8 -*-
"""Operator XDG config for plaibook (not committed; extra-vars still win)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping, TextIO

import yaml

CONFIG_DIRNAME = "ansible-plaibook"
VARS_NAME = "vars.yml"
FAMILIES = ("cursor", "claude", "gemini", "openai", "claude_cli")
CURSOR_DEFAULT_MODEL = "gpt-5.6-luna"
CURSOR_DEFAULT_EFFORT = "high"
_CREDENTIAL_ENV = {
    "cursor": "CURSOR_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "claude_cli": "CLAUDE_CODE_OAUTH_TOKEN",
}


def vars_path(*, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    environ = os.environ if env is None else env
    xdg = (environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        base = Path(xdg).expanduser()
    else:
        base = Path(home if home is not None else Path.home()) / ".config"
    return base / CONFIG_DIRNAME / VARS_NAME


def load_vars(*, path: Path | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    target = path if path is not None else vars_path(env=env)
    if not target.is_file():
        return {}
    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    return dict(loaded) if isinstance(loaded, dict) else {}


def save_vars(
    updates: Mapping[str, Any],
    *,
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    target = path if path is not None else vars_path(env=env)
    current = load_vars(path=target)
    current.update(dict(updates))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(current, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return target


def cursor_defaults() -> dict[str, str]:
    return {
        "review_cursor_model": CURSOR_DEFAULT_MODEL,
        "review_cursor_effort": CURSOR_DEFAULT_EFFORT,
    }


def provider_payload(family: str) -> dict[str, str]:
    payload = {"agent_family": family}
    if family == "cursor":
        payload.update(cursor_defaults())
    return payload


def credential_present(family: str, env: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if env is None else env
    key = _CREDENTIAL_ENV.get(family)
    if family == "gemini":
        return bool((environ.get("GEMINI_API_KEY") or environ.get("GOOGLE_API_KEY") or "").strip())
    if not key:
        return False
    return bool((environ.get(key) or "").strip())


def default_family(env: Mapping[str, str] | None = None) -> str:
    environ = os.environ if env is None else env
    if credential_present("cursor", environ):
        return "cursor"
    for family in FAMILIES:
        if credential_present(family, environ):
            return family
    return "claude"


def parse_family(raw: str) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(FAMILIES):
            return FAMILIES[index - 1]
        return None
    if text in FAMILIES:
        return text
    return None


def prompt_family(
    *,
    stdin: TextIO,
    stderr: TextIO,
    env: Mapping[str, str] | None = None,
    default: str | None = None,
) -> str:
    environ = os.environ if env is None else env
    chosen_default = default or default_family(environ)
    stderr.write("Select a review provider (saved to ~/.config/ansible-plaibook/vars.yml):\n")
    for index, family in enumerate(FAMILIES, start=1):
        mark = "  (credential detected)" if credential_present(family, environ) else ""
        stderr.write(f"  {index}) {family}{mark}\n")
    stderr.write(f"Provider [{chosen_default}]: ")
    stderr.flush()
    try:
        typed = stdin.readline()
    except EOFError:
        typed = ""
    parsed = parse_family(typed)
    return parsed or chosen_default


def persist_family(family: str, *, env: Mapping[str, str] | None = None) -> Path:
    current = load_vars(env=env)
    payload = {"agent_family": family}
    if family == "cursor":
        if not current.get("review_cursor_model"):
            payload["review_cursor_model"] = CURSOR_DEFAULT_MODEL
        if not current.get("review_cursor_effort"):
            payload["review_cursor_effort"] = CURSOR_DEFAULT_EFFORT
    return save_vars(payload, env=env)


def openshell_available(python: str | None = None) -> bool:
    import importlib.util

    if python and Path(python).resolve() != Path(sys.executable).resolve():
        return False
    return importlib.util.find_spec("openshell") is not None


def running_inside_openshell(env: Mapping[str, str] | None = None) -> bool:
    """True when this process is already an OpenShell sandbox.

    Nested `use_sandbox=true` would create a second sandbox via the gateway.
    Isolation is already in place; skip that unless the operator passes
    --sandbox.
    """
    environ = os.environ if env is None else env
    if (environ.get("OPENSHELL_SANDBOX") or "").strip():
        return True
    if (environ.get("OPENSHELL_SANDBOX_ID") or "").strip():
        return True
    if (environ.get("OPENSHELL_ENDPOINT") or "").strip():
        return True
    return Path("/etc/openshell/auth/sandbox.jwt").is_file()


def resolve_family(
    *,
    cli_family: str | None,
    stdin: TextIO,
    stderr: TextIO,
    env: Mapping[str, str] | None = None,
    interactive: bool | None = None,
) -> str | None:
    """Pick agent_family for this machine. None means leave it to Ansible."""
    environ = os.environ if env is None else env
    if cli_family:
        family = parse_family(cli_family)
        if family is None:
            raise ValueError(f"unknown provider {cli_family!r}; choose one of: {', '.join(FAMILIES)}")
        persist_family(family, env=environ)
        stderr.write(f"Saved provider {family} to {vars_path(env=environ)}\n")
        return family

    configured = load_vars(env=environ).get("agent_family")
    if configured in FAMILIES:
        return None
    env_family = (environ.get("ANSIBLE_REVIEW_AGENT_FAMILY") or "").strip()
    if env_family:
        return None

    tty = stdin.isatty() if interactive is None else interactive
    if tty:
        family = prompt_family(stdin=stdin, stderr=stderr, env=environ)
        persist_family(family, env=environ)
        stderr.write(f"Saved provider {family} to {vars_path(env=environ)}\n")
        return family

    if credential_present("cursor", environ):
        family = "cursor"
        persist_family(family, env=environ)
        stderr.write(
            f"No TTY; using cursor (CURSOR_API_KEY is set). Saved to {vars_path(env=environ)}\n"
        )
        return family
    return None
