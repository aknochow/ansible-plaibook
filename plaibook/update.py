# -*- coding: utf-8 -*-
"""Update the pipx-managed plaibook install from PyPI or a GitHub ref."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from plaibook import __version__
from plaibook.playbook import last_run_dir

LOCK_NAME = "updates.lock"
PYPI_JSON_API = "https://pypi.org/pypi/plaibook/json"
GITHUB_REPO = "aknochow/ansible-plaibook"
DOWNLOAD_TIMEOUT_SECONDS = 600
PYPI_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0


class UpdateError(RuntimeError):
    """Base class for update errors."""


class NetworkError(UpdateError):
    """Network/download failures."""


class VersionError(UpdateError):
    """Version comparison/validation failures."""


def update_lock_path(home: Path | None = None) -> Path:
    """Return path to updates.lock file for serializing update operations."""
    return last_run_dir(home) / LOCK_NAME


@contextmanager
def _exclusive_update_lock(home: Path | None = None) -> Iterator[None]:
    """Serialize concurrent update operations on this machine.

    Pattern from collections.py:_exclusive_collections_lock.
    """
    lock_path = update_lock_path(home)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        raise UpdateError(f"Cannot open update lock {lock_path}: {exc}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateError(
                "Another plai update is already running. "
                "The lock is released when that process exits."
            ) from exc
        yield
    finally:
        os.close(fd)


def current_version() -> str:
    """Return the version of the plaibook module this process imported."""
    return __version__


def _direct_url() -> dict | None:
    """PEP 610 direct_url.json for the plaibook distribution, if it has one."""
    try:
        from importlib.metadata import PackageNotFoundError, distribution

        dist = distribution("plaibook")
    except PackageNotFoundError:
        return None
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def running_install_kind(prefix: str | None = None) -> str:
    """How the plaibook process that is running was installed.

    ``editable`` is a checkout (``pip install -e``) and is not replaced.
    ``pipx`` and ``uv`` are tool installs. ``venv`` is a virtualenv whose
    plaibook came from pip. ``unknown`` is anything else.
    """
    direct = _direct_url()
    info = direct.get("dir_info") if isinstance(direct, dict) else None
    if isinstance(info, dict) and info.get("editable"):
        return "editable"

    root = Path(prefix if prefix is not None else sys.prefix).resolve()
    parts = set(root.parts)
    cfg = root / "pyvenv.cfg"
    cfg_text = cfg.read_text(errors="replace") if cfg.is_file() else ""
    if ("pipx" in parts and "venvs" in parts) or "pipx" in cfg_text:
        return "pipx"
    if ("uv" in parts and "tools" in parts) or "\nuv =" in f"\n{cfg_text}":
        return "uv"
    if prefix is None and sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return "venv"
    return "unknown"


def git_ref_spec(ref: str) -> str:
    """The git+https requirement pipx and uv record for this repository."""
    ref = _validate_github_ref(ref)
    return f"git+https://github.com/{GITHUB_REPO}.git@{ref}"


def spec_matches_git_ref(spec: str | None, ref: str) -> bool:
    """True when a recorded requirement is exactly that git ref."""
    expected = git_ref_spec(ref)
    if not isinstance(spec, str):
        return False
    return spec == expected or spec.startswith(expected + "#")


_PYPI_DOWNLOAD_HOSTS = frozenset({"files.pythonhosted.org", "pypi.org", "pypi.python.org"})


def recorded_spec_is_pypi(spec: str | None) -> bool:
    """True only for an index install or a file actually hosted on PyPI.

    None is only a confirmed absence of direct_url.json, which is a
    normal index install. A failed read must not be passed as None.
    ``git+``, ``file:``, and any other host are not PyPI, even when the
    installed version string matches the current release.
    """
    if spec is None:
        return True
    if not isinstance(spec, str):
        return False
    text = spec.strip()
    if not text or text.startswith("git+") or text.lower().startswith("file:"):
        return False
    if text == "plaibook" or text.startswith("plaibook=="):
        return True
    if "://" not in text:
        return False
    host = (urlsplit(text).hostname or "").lower()
    return host in _PYPI_DOWNLOAD_HOSTS


def _parse_pypi_version(raw: bytes) -> str:
    """Return info.version, or raise NetworkError for a malformed body."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError) as exc:
        raise NetworkError(f"PyPI returned invalid JSON: {exc}") from exc
    info = payload.get("info") if isinstance(payload, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise NetworkError("PyPI returned invalid JSON: version was not a string")
    return version


def fetch_pypi_latest_version(timeout: int = PYPI_TIMEOUT_SECONDS) -> str:
    """Query PyPI JSON API and return the latest published version.

    Raises:
        NetworkError: If PyPI is unreachable or returns invalid JSON.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(PYPI_JSON_API, headers={"User-Agent": f"plaibook/{__version__}"})
            with urlopen(req, timeout=timeout) as response:
                return _parse_pypi_version(response.read())
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt == MAX_RETRIES:
                raise NetworkError(
                    f"Failed to fetch PyPI metadata after {MAX_RETRIES} attempts: {exc}"
                ) from exc
            wait = RETRY_BACKOFF_BASE ** (attempt - 1)
            time.sleep(wait)
    # Should not reach here
    raise NetworkError("Failed to fetch PyPI metadata")


def _validate_github_ref(ref: str) -> str:
    """Validate and sanitize GitHub ref to prevent path traversal.

    Returns the sanitized ref.
    Raises UpdateError if the ref is invalid.
    """
    ref = ref.strip()
    if not ref:
        raise UpdateError("GitHub ref cannot be empty")

    # A branch name may contain slashes. Reject a leading or trailing
    # slash and any parent-directory segment.
    if ".." in ref or "/" in ref.replace("/", "", 1):
        if ref.startswith("/") or ref.endswith("/") or "../" in ref or "/.." in ref:
            raise UpdateError(f"Invalid GitHub ref (path traversal attempt): {ref}")

    return ref


def pipx_executable() -> str:
    """Return the pipx binary, or raise if this machine cannot upgrade a pipx install."""
    found = shutil.which("pipx")
    if not found:
        raise UpdateError(
            "pipx is not on PATH. Install plaibook with `pipx install plaibook`, then run plai update."
        )
    return found


def _run_pipx(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a pipx command and capture its output."""
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"pipx timed out after {DOWNLOAD_TIMEOUT_SECONDS}s") from exc


_PYPI_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")


def pipx_install_pypi(version: str) -> subprocess.CompletedProcess[str]:
    """Install that exact plaibook release from PyPI, replacing any git source.

    ``pipx upgrade plaibook`` follows the spec pipx already recorded. After
    ``plai update --branch``, that spec is a git ref, so the default path
    must install ``plaibook==VERSION`` instead. ``--no-cache-dir`` is passed
    through because the operator sandbox cannot write pip's cache.
    """
    if not _PYPI_VERSION.fullmatch(version) or ".." in version:
        raise UpdateError(f"Invalid PyPI version: {version}")
    return _run_pipx([
        pipx_executable(),
        "install",
        "--force",
        "--pip-args=--no-cache-dir",
        f"plaibook=={version}",
    ])


def pipx_installed_version() -> str | None:
    """Return the plaibook version inside the pipx venv, not this process."""
    result = _run_pipx([pipx_executable(), "runpip", "plaibook", "show", "plaibook"])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _unreadable_pipx_spec(reason: str) -> UpdateError:
    """An unreadable list is not evidence that the install came from PyPI."""
    return UpdateError(
        "Could not read plaibook's pipx spec "
        f"({reason}), so this command will not treat the install as a PyPI release."
    )


def pipx_package_spec() -> str | None:
    """Return the spec pipx recorded for plaibook.

    A PyPI install is ``plaibook``. ``plai update --branch`` records a git
    URL. None means pipx ran and has no plaibook venv. That is not a PyPI
    install.

    Raises UpdateError when pipx is missing or its list cannot be read.
    """
    if shutil.which("pipx") is None:
        raise UpdateError(
            "pipx is not on PATH. Install plaibook with `pipx install plaibook`, "
            "then run plai update."
        )
    result = _run_pipx([pipx_executable(), "list", "--json"])
    if result.returncode != 0 or not (result.stdout or "").strip():
        raise _unreadable_pipx_spec("pipx list --json failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _unreadable_pipx_spec("pipx list --json was not JSON") from exc
    venvs = payload.get("venvs") if isinstance(payload, dict) else None
    if not isinstance(venvs, dict):
        raise _unreadable_pipx_spec("pipx list --json has no venvs object")
    if "plaibook" not in venvs:
        return None
    entry = venvs.get("plaibook")
    metadata = entry.get("metadata") if isinstance(entry, dict) else None
    main = metadata.get("main_package") if isinstance(metadata, dict) else None
    spec = main.get("package_or_url") if isinstance(main, dict) else None
    if not isinstance(spec, str) or not spec.strip():
        raise _unreadable_pipx_spec("plaibook's package_or_url is missing")
    return spec.strip()


def pipx_spec_is_pypi(spec: str | None) -> bool:
    """True only when pipx recorded a PyPI name for plaibook.

    None means the list succeeded and plaibook has no venv. That is not
    a PyPI install, and a missing or unreadable pipx raises instead of
    returning None.
    """
    if spec is None:
        return False
    return spec == "plaibook" or spec.startswith("plaibook==")


def pipx_install_git_ref(ref: str) -> subprocess.CompletedProcess[str]:
    """Install a GitHub ref into the pipx environment, replacing any existing plaibook.

    ``pipx upgrade`` cannot switch sources. ``pipx install --force`` records
    this spec and passes ``--no-cache-dir`` to pip. The operator sandbox
    cannot write pip's cache. A later ``plai update`` with no flags installs
    the PyPI release instead, so the git spec does not stick.
    """
    git_url = git_ref_spec(ref)
    # Reject user:token@host before the ref separator.
    if "@" in git_url.split("@")[0]:
        raise UpdateError("git URL must not contain embedded credentials")
    return _run_pipx([
        pipx_executable(),
        "install",
        "--force",
        "--pip-args=--no-cache-dir",
        git_url,
    ])


def uv_executable() -> str:
    """Return the uv binary, or raise if this uv tool install cannot be upgraded."""
    found = shutil.which("uv")
    if not found:
        raise UpdateError(
            "uv is not on PATH. This plaibook was installed with `uv tool install plaibook`."
        )
    return found


def _uv_tool_python() -> Path:
    """Python inside the uv tool environment that is running this process."""
    return Path(sys.prefix) / "bin" / "python"


def _run_python(python: Path, code: str) -> str | None:
    """Run a short snippet. None when the interpreter is missing or the snippet fails."""
    if not python.is_file():
        return None
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    return text or None


def uv_install_pypi(version: str) -> subprocess.CompletedProcess[str]:
    """Install that exact release into the uv tool environment."""
    if not _PYPI_VERSION.fullmatch(version) or ".." in version:
        raise UpdateError(f"Invalid PyPI version: {version}")
    return _run_pipx([
        uv_executable(),
        "tool",
        "install",
        "--force",
        "--no-cache",
        f"plaibook=={version}",
    ])


def uv_install_git_ref(ref: str) -> subprocess.CompletedProcess[str]:
    """Install a GitHub ref with uv tool, replacing the current tool env."""
    return _run_pipx([
        uv_executable(),
        "tool",
        "install",
        "--force",
        "--no-cache",
        git_ref_spec(ref),
    ])


def uv_installed_version() -> str | None:
    """Version of plaibook inside the uv tool environment, not this process."""
    return _run_python(
        _uv_tool_python(),
        "import importlib.metadata as m; print(m.version('plaibook'))",
    )


_DIRECT_URL_ABSENT = "plaibook-direct-url:absent"
_DIRECT_URL_PREFIX = "plaibook-direct-url:"
_DIRECT_URL_SNIPPET = (
    "import importlib.metadata as m\n"
    "text = m.distribution('plaibook').read_text('direct_url.json')\n"
    "print('plaibook-direct-url:absent' if text is None else 'plaibook-direct-url:' + text)\n"
)


def _spec_from_direct_url_text(raw: str) -> str:
    """Turn PEP 610 JSON into the requirement this command records.

    Raises UpdateError when the body is not a usable direct URL. Absence
    of the file is handled before this is called.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpdateError(
            "Could not read plaibook's install source (direct_url.json was not JSON), "
            "so this command will not treat the install as a PyPI release."
        ) from exc
    if not isinstance(payload, dict):
        raise UpdateError(
            "Could not read plaibook's install source (direct_url.json was not an object), "
            "so this command will not treat the install as a PyPI release."
        )
    url = payload.get("url")
    vcs = payload.get("vcs_info")
    revision = vcs.get("requested_revision") if isinstance(vcs, dict) else None
    if isinstance(url, str) and isinstance(revision, str) and revision.strip():
        base = url.strip()
        if base.startswith("git+"):
            base = base[len("git+"):]
        if not base.endswith(".git"):
            base = base + ".git"
        return f"git+{base}@{revision.strip()}"
    if isinstance(url, str) and url.strip():
        return url.strip()
    raise UpdateError(
        "Could not read plaibook's install source (direct_url.json did not name a URL), "
        "so this command will not treat the install as a PyPI release."
    )


def _unreadable_install_source() -> UpdateError:
    return UpdateError(
        "Could not read plaibook's install source, "
        "so this command will not treat the install as a PyPI release."
    )


def _recorded_spec(python: Path) -> str | None:
    """Return the recorded requirement, or None when direct_url.json is absent.

    None is not used for a failed read. A missing interpreter, a failed
    snippet, or unusable JSON raises UpdateError.
    """
    raw = _run_python(python, _DIRECT_URL_SNIPPET)
    if raw is None:
        raise _unreadable_install_source()
    if raw == _DIRECT_URL_ABSENT:
        return None
    if not raw.startswith(_DIRECT_URL_PREFIX):
        raise _unreadable_install_source()
    return _spec_from_direct_url_text(raw[len(_DIRECT_URL_PREFIX):])


def uv_recorded_spec() -> str | None:
    """Requirement recorded for plaibook in the uv tool environment."""
    return _recorded_spec(_uv_tool_python())


def venv_recorded_spec() -> str | None:
    """Requirement recorded for plaibook in this virtualenv."""
    return _recorded_spec(Path(sys.executable))


def venv_install_pypi(version: str) -> subprocess.CompletedProcess[str]:
    """Reinstall that release into the virtualenv that is running this process."""
    if not _PYPI_VERSION.fullmatch(version) or ".." in version:
        raise UpdateError(f"Invalid PyPI version: {version}")
    return _run_pipx([
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--force-reinstall",
        "--no-cache-dir",
        f"plaibook=={version}",
    ])


def venv_install_git_ref(ref: str) -> subprocess.CompletedProcess[str]:
    """Install a GitHub ref into the virtualenv that is running this process."""
    return _run_pipx([
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--force-reinstall",
        "--no-cache-dir",
        git_ref_spec(ref),
    ])


def venv_installed_version() -> str | None:
    """Version reported by this virtualenv's pip."""
    result = _run_pipx([
        sys.executable,
        "-m",
        "pip",
        "show",
        "--disable-pip-version-check",
        "plaibook",
    ])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip() or None
    return None


def prompt_confirm(message: str, default: bool = True) -> bool:
    """Ask for Y/n confirmation.

    Returns True if the user confirms, False otherwise.
    When stdin is not a terminal, returns False. Non-interactive updates
    must pass ``--yes``. The default applies only to an empty reply on a
    terminal.
    """
    if not sys.stdin.isatty():
        return False

    prompt_text = f"{message} [{'Y/n' if default else 'y/N'}] "
    try:
        response = input(prompt_text).strip().lower()
        if not response:
            return default
        return response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()  # newline after ^C
        return False
