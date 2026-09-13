# -*- coding: utf-8 -*-
"""Locate the playbook tree (checkout or bundled share) and run ansible-playbook."""

from __future__ import annotations

import math
import os
import secrets
import shutil
import signal
import socket
import stat
import string
import subprocess
import sys
import time
from pathlib import Path

PLAYBOOK_NAME = "review.yml"
ANSIBLE_CFG_NAME = "ansible.cfg"
ENV_ROOT = "PLAIBOOK_ROOT"
ENV_TIMEOUT = "PLAIBOOK_PLAYBOOK_TIMEOUT"
DEFAULT_PLAYBOOK_TIMEOUT_SECONDS = 3600
RUN_ID_CHARS = string.ascii_letters + string.digits
RUN_ID_LENGTH = 16
CACHE_DIRNAME = "ansible-plaibook"
RUNTIME_TMP_DIRNAME = "tmp"


class PlaybookNotFoundError(FileNotFoundError):
    """review.yml could not be located from this install."""


class ScratchDirError(OSError):
    """Review scratch under ~/.cache/ansible-plaibook/tmp is not private."""


class PlaybookTimeoutError(TimeoutError):
    """ansible-playbook exceeded PLAIBOOK_PLAYBOOK_TIMEOUT."""

    def __init__(self, seconds: float, command: list[str]):
        self.seconds = seconds
        self.command = command
        super().__init__(
            f"ansible-playbook exceeded {seconds:.0f}s timeout. "
            f"Set {ENV_TIMEOUT} to raise the limit (seconds)."
        )


def generate_run_id() -> str:
    """Match the playbook's password-lookup run_id alphabet and length."""
    return "".join(secrets.choice(RUN_ID_CHARS) for _ in range(RUN_ID_LENGTH))


def last_run_dir(home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".cache" / CACHE_DIRNAME


def runtime_tmp_dir(home: Path | None = None) -> Path:
    """Scratch for clones, checklists, and spinner files — not /tmp.

    Fail closed unless the directory is owned by this user and mode 0o700.
    mkdir is umask-filtered; chmod failures and preexisting open modes
    must not proceed. macOS XProtect treats newly-executed scripts under
    /tmp as droppers.
    """
    path = last_run_dir(home) / RUNTIME_TMP_DIRNAME
    if path.is_symlink():
        raise ScratchDirError(
            f"{path} is a symlink; plaibook will not use it as review scratch. "
            f"Remove the symlink so {CACHE_DIRNAME} can own this cache."
        )
    if path.exists() and not path.is_dir():
        raise ScratchDirError(
            f"{path} exists and is not a directory. Remove it so "
            f"{CACHE_DIRNAME} can own this cache."
        )
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
        info = os.lstat(path)
    except OSError as exc:
        raise ScratchDirError(
            f"Cannot make review scratch private ({path}). "
            "Fix permissions or remove the directory."
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ScratchDirError(
            f"{path} must be a real directory owned by this user."
        )
    if info.st_uid != os.geteuid():
        raise ScratchDirError(
            f"{path} is not owned by this user; plaibook will not write "
            "review scratch there."
        )
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ScratchDirError(
            f"{path} is not private (mode {stat.S_IMODE(info.st_mode):04o}); "
            "expected owner-only 0700."
        )
    return path


def last_run_path(run_id: str, home: Path | None = None) -> Path:
    return last_run_dir(home) / f"last_run.{run_id}.json"


def last_run_canonical_path(home: Path | None = None) -> Path:
    """Last-write-wins sibling of last_run.<run_id>.json."""
    return last_run_dir(home) / "last_run.json"


def bundled_playbook_root(package_dir: Path | None = None) -> Path:
    """Playbook tree vendored into the wheel at plaibook/share/."""
    here = package_dir if package_dir is not None else Path(__file__).resolve().parent
    return Path(here).resolve() / "share"


def find_playbook_root(
    start: Path | None = None,
    env: dict[str, str] | None = None,
    *,
    package_dir: Path | None = None,
) -> Path:
    """Find the tree that contains review.yml + ansible.cfg.

    ``pip install plaibook && plai review`` uses this install: an
    editable checkout (package parents) or the wheel's bundled share.
    ``PLAIBOOK_ROOT`` is a last resort when this install has no
    playbook, not a hijack of a working pip install. ``--root`` is the
    checkout override (handled by the CLI). cwd is never searched:
    a reviewed repo must not supply review.yml.
    ``start`` is accepted for call-site compatibility and ignored.
    """
    _ = start
    environ = os.environ if env is None else env
    here = (package_dir or Path(__file__).resolve().parent).resolve()
    seen: set[Path] = set()
    for candidate in here.parents:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _is_playbook_root(candidate):
            return candidate

    bundled = bundled_playbook_root(here)
    if _is_playbook_root(bundled):
        return bundled

    explicit = environ.get(ENV_ROOT, "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if _is_playbook_root(root):
            return root
        raise PlaybookNotFoundError(
            f"{ENV_ROOT}={root} does not contain {PLAYBOOK_NAME} and {ANSIBLE_CFG_NAME}"
        )

    raise PlaybookNotFoundError(
        "Could not find review.yml. Reinstall plaibook (`pip install plaibook`) "
        f"or pass --root / set {ENV_ROOT} to an ansible-plaibook checkout."
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


def ansible_tool_bin(name: str) -> str:
    """Prefer *name* next to this interpreter, even if python is a symlink.

    ``Path.resolve()`` follows ``.venv/bin/python`` into ``/usr/bin``, so the
    sibling lookup would miss ``.venv/bin/ansible-playbook`` and fall through
    to an unrelated PATH binary.
    """
    exe = Path(sys.executable)
    candidates = [exe.parent / name, exe.resolve().parent / name]
    seen: set[Path] = set()
    for sibling in candidates:
        if sibling in seen:
            continue
        seen.add(sibling)
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"{name} not found next to this interpreter or on PATH. "
        "Reinstall plaibook (`pip install plaibook`); ansible-core is a dependency."
    )


def ansible_playbook_bin() -> str:
    return ansible_tool_bin("ansible-playbook")


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


def playbook_timeout_seconds(env: dict[str, str] | None = None) -> float:
    """Seconds ansible-playbook may run before the CLI kills the process group."""
    environ = os.environ if env is None else env
    raw = (environ.get(ENV_TIMEOUT) or "").strip()
    if not raw:
        return float(DEFAULT_PLAYBOOK_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_TIMEOUT}={raw!r} must be a positive number of seconds") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{ENV_TIMEOUT}={raw!r} must be a positive finite number of seconds")
    return value


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


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
    home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ansible-playbook. Quiet mode captures output; -v inherits the TTY."""
    from plaibook.collections import merge_collections_path

    timeout = playbook_timeout_seconds()
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged = enrich_review_env(merged)
    merged["ANSIBLE_CONFIG"] = str(playbook_root / ANSIBLE_CFG_NAME)
    merged["TMPDIR"] = str(runtime_tmp_dir(home))
    merge_collections_path(merged, home=home)
    kwargs: dict = {
        "args": command,
        "env": merged,
        "text": True,
        "start_new_session": True,
    }
    if not verbose:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    proc = subprocess.Popen(**kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(proc)
        raise PlaybookTimeoutError(timeout, command) from exc
    return subprocess.CompletedProcess(command, proc.returncode, stdout or "", stderr or "")
