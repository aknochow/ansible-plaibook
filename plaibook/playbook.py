# -*- coding: utf-8 -*-
"""Locate the plaibook checkout and shell out to ansible-playbook."""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import string
import subprocess
import sys
import time
from pathlib import Path

PLAYBOOK_NAME = "review.yml"
ANSIBLE_CFG_NAME = "ansible.cfg"
ENV_ROOT = "PLAIBOOK_ROOT"
RUN_ID_CHARS = string.ascii_letters + string.digits
RUN_ID_LENGTH = 16
CACHE_DIRNAME = "ansible-plaibook"


class PlaybookNotFoundError(FileNotFoundError):
    """review.yml could not be located from this install."""


def generate_run_id() -> str:
    """Match the playbook's password-lookup run_id alphabet and length."""
    return "".join(secrets.choice(RUN_ID_CHARS) for _ in range(RUN_ID_LENGTH))


def last_run_path(run_id: str, home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".cache" / CACHE_DIRNAME / f"last_run.{run_id}.json"


def find_playbook_root(start: Path | None = None, env: dict[str, str] | None = None) -> Path:
    """Find the checkout that contains review.yml + ansible.cfg.

    v1 is an editable install from a plaibook checkout. Search order:
    PLAIBOOK_ROOT, then parents of this package, then parents of *start* (cwd).
    """
    environ = os.environ if env is None else env
    explicit = environ.get(ENV_ROOT, "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if _is_playbook_root(root):
            return root
        raise PlaybookNotFoundError(
            f"{ENV_ROOT}={root} does not contain {PLAYBOOK_NAME} and {ANSIBLE_CFG_NAME}"
        )

    candidates: list[Path] = []
    here = Path(__file__).resolve().parent
    candidates.extend(here.parents)
    origin = (start or Path.cwd()).resolve()
    candidates.append(origin)
    candidates.extend(origin.parents)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _is_playbook_root(candidate):
            return candidate

    raise PlaybookNotFoundError(
        "Could not find review.yml. v1 of the plaibook CLI needs an editable "
        "install from a checkout (`pip install -e .` / `uv sync`). Set "
        f"{ENV_ROOT} to the ansible-plaibook checkout, or run from inside it."
    )


def _is_playbook_root(path: Path) -> bool:
    return (path / PLAYBOOK_NAME).is_file() and (path / ANSIBLE_CFG_NAME).is_file()


HTTP1_PROXY_HOST = "127.0.0.1"
HTTP1_PROXY_PORT = 18080
HTTP1_PROXY_URL = f"http://{HTTP1_PROXY_HOST}:{HTTP1_PROXY_PORT}"


def http1_proxy_listening(*, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((HTTP1_PROXY_HOST, HTTP1_PROXY_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _cursor_http1_proxy_script() -> Path | None:
    bundled = Path(__file__).resolve().parent / "cursor_http1_proxy.js"
    if bundled.is_file():
        return bundled
    fallback = Path("/sandbox/api2-http1-proxy.js")
    return fallback if fallback.is_file() else None


def _vendor_node_bin() -> str | None:
    try:
        from cursor_sdk._vendor import resolve_bridge_path

        node = Path(resolve_bridge_path()).resolve().parent / "node"
        if node.is_file() and os.access(node, os.X_OK):
            return str(node)
    except Exception:
        pass
    found = shutil.which("node")
    return found


def ensure_http1_proxy() -> bool:
    """Make sure 127.0.0.1:18080 is accepting HTTP/1.1 to api2.cursor.sh.

    The vendor node cannot speak HTTP/2 on this OpenShell host. A helper
    process is started when nothing is listening yet.
    """
    if http1_proxy_listening():
        return True
    script = _cursor_http1_proxy_script()
    node = _vendor_node_bin()
    if script is None or node is None:
        return False
    log_dir = Path.home() / ".cache" / CACHE_DIRNAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "http1-proxy.log"
    with log_path.open("ab") as log:
        subprocess.Popen(
            [node, str(script)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PLAIBOOK_HTTP1_PROXY_PORT": str(HTTP1_PROXY_PORT)},
        )
    for _ in range(25):
        time.sleep(0.1)
        if http1_proxy_listening():
            return True
    return False


def ansible_playbook_bin() -> str:
    sibling = Path(sys.executable).resolve().parent / "ansible-playbook"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    found = shutil.which("ansible-playbook")
    if found:
        return found
    raise FileNotFoundError(
        "ansible-playbook not found next to this interpreter or on PATH. "
        "Install from the plaibook checkout with `uv sync` / `pip install -e .`."
    )


def build_ansible_command(
    *,
    extra_vars: dict,
    playbook_root: Path,
    ansible_bin: str | None = None,
    verbosity: int = 0,
) -> list[str]:
    import json

    playbook = playbook_root / PLAYBOOK_NAME
    command = [ansible_bin or ansible_playbook_bin(), str(playbook)]
    if verbosity > 0:
        command.append("-" + ("v" * min(int(verbosity), 4)))
    command.extend(["-e", json.dumps(extra_vars, separators=(",", ":"))])
    return command


def enrich_review_env(env: dict[str, str]) -> dict[str, str]:
    """Fill in local-dev helpers the playbook child needs, if they are present.

    Inside an OpenShell sandbox, a login shell may already export
    CURSOR_BACKEND_URL at api2.cursor.sh (HTTP/2). That fails here; the
    local HTTP/1.1 proxy on 127.0.0.1:18080 wins when it is listening.
    On a normal laptop, an operator-set CURSOR_BACKEND_URL is left alone.
    """
    lib = Path("/sandbox/libdevshm.so")
    if lib.is_file() and not (env.get("LD_PRELOAD") or "").strip():
        env["LD_PRELOAD"] = str(lib)
        Path("/tmp/openshell-shm").mkdir(parents=True, exist_ok=True)
    from plaibook.config import resolve_cursor_api_key, running_inside_openshell

    inside = running_inside_openshell(env)
    proxy_up = ensure_http1_proxy() if inside else http1_proxy_listening()
    # Inside this OpenShell host the vendor node cannot speak HTTP/2 to
    # api2.cursor.sh. Always pin the local HTTP/1.1 proxy when it is up;
    # a login shell may already export CURSOR_BACKEND_URL at api2.
    if proxy_up and (inside or not (env.get("CURSOR_BACKEND_URL") or "").strip()):
        env["CURSOR_BACKEND_URL"] = HTTP1_PROXY_URL
        # The vendor node must talk to 127.0.0.1:18080 directly. Login
        # shells here export NODE_USE_ENV_PROXY=1 and http_proxy; if
        # those apply to loopback, the SDK fails with
        # "Network request failed".
        env["NODE_USE_ENV_PROXY"] = "0"
        existing_no_proxy = env.get("no_proxy") or env.get("NO_PROXY") or ""
        merged_no_proxy = ",".join(
            dict.fromkeys(
                [p.strip() for p in existing_no_proxy.split(",") if p.strip()]
                + ["127.0.0.1", "localhost", "::1"]
            )
        )
        env["no_proxy"] = merged_no_proxy
        env["NO_PROXY"] = merged_no_proxy
    ca = Path("/etc/openshell-tls/openshell-ca.pem")
    if ca.is_file() and not (env.get("GIT_SSL_CAINFO") or "").strip():
        env["GIT_SSL_CAINFO"] = str(ca)
        env.setdefault("NODE_EXTRA_CA_CERTS", str(ca))
    collections = Path.home() / ".ansible" / "collections"
    if collections.is_dir() and not (env.get("ANSIBLE_COLLECTIONS_PATH") or "").strip():
        env["ANSIBLE_COLLECTIONS_PATH"] = str(collections)
    # OpenShell's login bash exports CURSOR_API_KEY as an OpenShell token
    # (`openshell...`). The SDK then fails with "Network request failed".
    # Prefer a real Cursor key from env or ~/.config/cursor/auth.json.
    key = resolve_cursor_api_key(env)
    if key:
        env["CURSOR_API_KEY"] = key
    else:
        env.pop("CURSOR_API_KEY", None)
    if not (env.get("CURSOR_SDK_BRIDGE_BIN") or "").strip():
        try:
            from cursor_sdk._vendor import resolve_bridge_path

            env["CURSOR_SDK_BRIDGE_BIN"] = resolve_bridge_path()
        except Exception:
            pass
    # Login-shell plai is already inside OpenShell but CURSOR_AGENT is
    # unset, so review.yml would keep in-process launch_bridge. Nested
    # vendor-node bring-up fails here with "Network request failed"; the
    # playbook sidecar (CURSOR_AGENT=1) is the working path. iTerm/CI
    # outside OpenShell are left alone.
    if running_inside_openshell(env) and (env.get("CURSOR_AGENT") or "").strip() != "1":
        env["CURSOR_AGENT"] = "1"
    return env


def run_ansible_playbook(
    command: list[str],
    *,
    playbook_root: Path,
    verbose: bool,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ansible-playbook. Quiet mode captures output; -v inherits the TTY."""
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged = enrich_review_env(merged)
    merged["ANSIBLE_CONFIG"] = str(playbook_root / ANSIBLE_CFG_NAME)
    if verbose:
        return subprocess.run(command, env=merged, check=False, text=True)
    return subprocess.run(
        command,
        env=merged,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
