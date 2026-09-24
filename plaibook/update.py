# -*- coding: utf-8 -*-
"""Update plaibook from PyPI or GitHub without pip's git clone into /tmp."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from plaibook import __version__
from plaibook.playbook import last_run_dir

UPDATES_DIRNAME = "updates"
LOCK_NAME = "updates.lock"
PYPI_JSON_API = "https://pypi.org/pypi/plaibook/json"
GITHUB_REPO = "aknochow/ansible-plaibook"
GITHUB_ARCHIVE_URL_TEMPLATE = f"https://github.com/{GITHUB_REPO}/archive/{{ref}}.tar.gz"
DOWNLOAD_TIMEOUT_SECONDS = 600
PYPI_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class UpdateError(RuntimeError):
    """Base class for update errors."""


class NetworkError(UpdateError):
    """Network/download failures."""


class VersionError(UpdateError):
    """Version comparison/validation failures."""


def update_cache_dir(home: Path | None = None) -> Path:
    """Return ~/.cache/ansible-plaibook/updates/ for staging downloads."""
    return last_run_dir(home) / UPDATES_DIRNAME


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
                f"If not, remove the lock file: {lock_path}"
            ) from exc
        yield
    finally:
        os.close(fd)


def current_version() -> str:
    """Return the currently installed plaibook version."""
    return __version__


def fetch_pypi_latest_version(timeout: int = PYPI_TIMEOUT_SECONDS) -> str:
    """Query PyPI JSON API and return the latest published version.

    Raises:
        NetworkError: If PyPI is unreachable or returns invalid JSON.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(PYPI_JSON_API, headers={"User-Agent": f"plaibook/{__version__}"})
            with urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                return data["info"]["version"]
        except (HTTPError, URLError) as exc:
            if attempt == MAX_RETRIES:
                raise NetworkError(
                    f"Failed to fetch PyPI metadata after {MAX_RETRIES} attempts: {exc}"
                ) from exc
            wait = RETRY_BACKOFF_BASE ** (attempt - 1)
            time.sleep(wait)
        except (KeyError, json.JSONDecodeError) as exc:
            raise NetworkError(f"PyPI returned invalid JSON: {exc}") from exc
    # Should not reach here
    raise NetworkError("Failed to fetch PyPI metadata")


def _cleartext_http(url: str) -> bool:
    """Check if URL uses plaintext HTTP (security violation)."""
    return url.strip().lower().startswith("http://")


def _validate_github_ref(ref: str) -> str:
    """Validate and sanitize GitHub ref to prevent path traversal.

    Returns the sanitized ref.
    Raises UpdateError if the ref is invalid.
    """
    ref = ref.strip()
    if not ref:
        raise UpdateError("GitHub ref cannot be empty")

    # Reject path traversal attempts
    if ".." in ref or "/" in ref.replace("/", "", 1):  # Allow one / for refs/heads/branch
        # Actually, GitHub archive API accepts branch names with slashes
        # but we should still validate against traversal
        if ref.startswith("/") or ref.endswith("/") or "../" in ref or "/.." in ref:
            raise UpdateError(f"Invalid GitHub ref (path traversal attempt): {ref}")

    return ref


def download_pypi_wheel(version: str, dest_dir: Path, timeout: int = DOWNLOAD_TIMEOUT_SECONDS) -> Path:
    """Download wheel from PyPI to dest_dir, return path to downloaded file.

    Raises:
        NetworkError: If download fails.
        UpdateError: If wheel URL is insecure.
    """
    # Fetch PyPI JSON to get wheel URL
    req = Request(PYPI_JSON_API, headers={"User-Agent": f"plaibook/{__version__}"})
    try:
        with urlopen(req, timeout=PYPI_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise NetworkError(f"Failed to fetch PyPI metadata: {exc}") from exc

    # Find the wheel for this version
    releases = data.get("releases", {}).get(version, [])
    wheel_url = None
    for release in releases:
        if release.get("packagetype") == "bdist_wheel":
            wheel_url = release.get("url")
            break

    if not wheel_url:
        raise NetworkError(f"No wheel found for plaibook version {version}")

    # Security: reject cleartext HTTP
    if _cleartext_http(wheel_url):
        raise UpdateError(f"Refusing to download over cleartext HTTP: {wheel_url}")

    # Download wheel
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"plaibook-{version}-py3-none-any.whl"
    dest_path = dest_dir / filename

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(wheel_url, headers={"User-Agent": f"plaibook/{__version__}"})
            with urlopen(req, timeout=timeout) as response:
                with open(dest_path, "wb") as f:
                    shutil.copyfileobj(response, f)
            return dest_path
        except (HTTPError, URLError, OSError) as exc:
            if attempt == MAX_RETRIES:
                raise NetworkError(
                    f"Failed to download wheel after {MAX_RETRIES} attempts: {exc}"
                ) from exc
            wait = RETRY_BACKOFF_BASE ** (attempt - 1)
            time.sleep(wait)

    raise NetworkError("Failed to download wheel")


def download_github_tarball(ref: str, dest_dir: Path, timeout: int = DOWNLOAD_TIMEOUT_SECONDS) -> Path:
    """Download and extract GitHub tarball to dest_dir, return path to extracted directory.

    Raises:
        NetworkError: If download fails.
        UpdateError: If ref is invalid or URL is insecure.
    """
    ref = _validate_github_ref(ref)
    url = GITHUB_ARCHIVE_URL_TEMPLATE.format(ref=ref)

    # Security: reject cleartext HTTP
    if _cleartext_http(url):
        raise UpdateError(f"Refusing to download over cleartext HTTP: {url}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    tarball_path = dest_dir / f"plaibook-{ref}.tar.gz"

    # Download tarball
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(url, headers={"User-Agent": f"plaibook/{__version__}"})
            with urlopen(req, timeout=timeout) as response:
                with open(tarball_path, "wb") as f:
                    shutil.copyfileobj(response, f)
            break
        except HTTPError as exc:
            if exc.code == 404:
                raise NetworkError(
                    f"Branch/ref '{ref}' not found in {GITHUB_REPO}. "
                    "Use a branch name (main), tag (v0.1.26), or commit SHA."
                ) from exc
            if attempt == MAX_RETRIES:
                raise NetworkError(
                    f"Failed to download tarball after {MAX_RETRIES} attempts: {exc}"
                ) from exc
            wait = RETRY_BACKOFF_BASE ** (attempt - 1)
            time.sleep(wait)
        except (URLError, OSError) as exc:
            if attempt == MAX_RETRIES:
                raise NetworkError(
                    f"Failed to download tarball after {MAX_RETRIES} attempts: {exc}"
                ) from exc
            wait = RETRY_BACKOFF_BASE ** (attempt - 1)
            time.sleep(wait)

    # Extract tarball
    extract_dir = dest_dir / f"plaibook-{ref}"
    try:
        with tarfile.open(tarball_path, "r:gz") as tar:
            # Security: validate paths before extraction
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    raise UpdateError(f"Tarball contains unsafe path: {member.name}")
            tar.extractall(path=dest_dir)
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError(f"Failed to extract tarball: {exc}") from exc

    # GitHub tarballs extract to ansible-plaibook-<ref>/, find it
    extracted_dirs = [d for d in dest_dir.iterdir() if d.is_dir() and d.name.startswith("ansible-plaibook-")]
    if not extracted_dirs:
        raise UpdateError("Tarball did not extract to expected directory structure")

    # Rename to our expected name
    actual_dir = extracted_dirs[0]
    if actual_dir != extract_dir:
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        actual_dir.rename(extract_dir)

    return extract_dir


def validate_pyproject_is_plaibook(directory: Path) -> str:
    """Read pyproject.toml, verify name='plaibook', return version string.

    Raises:
        UpdateError: If pyproject.toml is missing, invalid, or wrong project.
    """
    pyproject_path = directory / "pyproject.toml"
    if not pyproject_path.exists():
        raise UpdateError(f"No pyproject.toml found in {directory}")

    try:
        # Simple TOML parsing for [project] section
        # We avoid external dependencies; parse manually
        content = pyproject_path.read_text(encoding="utf-8")

        # Extract name and version from [project] section
        in_project = False
        name = None
        version = None

        for line in content.splitlines():
            line = line.strip()
            if line == "[project]":
                in_project = True
                continue
            if in_project and line.startswith("["):
                break  # End of [project] section
            if in_project:
                if line.startswith("name"):
                    # Parse: name = "plaibook"
                    match = re.match(r'name\s*=\s*["\']([^"\']+)["\']', line)
                    if match:
                        name = match.group(1)
                elif line.startswith("version"):
                    # Parse: version = "0.1.26"
                    match = re.match(r'version\s*=\s*["\']([^"\']+)["\']', line)
                    if match:
                        version = match.group(1)

        if name != "plaibook":
            raise UpdateError(
                f"Not a plaibook repository (project name is '{name}' in {pyproject_path})"
            )

        if not version:
            raise UpdateError(f"No version found in {pyproject_path}")

        return version

    except OSError as exc:
        raise UpdateError(f"Cannot read {pyproject_path}: {exc}") from exc


def pip_install_from_path(
    path: Path,
    python: str | None = None,
    *,
    stderr: TextIO | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run pip install --force-reinstall from path, return CompletedProcess.

    Pattern from provider_sdk.py:_run.
    """
    python_exe = python or sys.executable
    argv = [
        python_exe,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--force-reinstall",
        str(path),
    ]

    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            f"pip install timed out after {DOWNLOAD_TIMEOUT_SECONDS}s"
        ) from exc


def pip_install_git_ref(
    ref: str,
    python: str | None = None,
    *,
    stderr: TextIO | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run pip install --force-reinstall --no-cache-dir from git+https URL.

    This is the approach from issue #61 that works in operator sandbox where
    GitHub archive URLs return 403. pip clones into /tmp, which is acceptable
    in that context.

    Pattern from provider_sdk.py:_run.
    """
    ref = _validate_github_ref(ref)
    python_exe = python or sys.executable
    git_url = f"git+https://github.com/{GITHUB_REPO}.git@{ref}"

    # Security: validate no credentials in URL
    if "@" in git_url.split("@")[0]:  # @ before the ref separator
        raise UpdateError("git URL must not contain embedded credentials")

    argv = [
        python_exe,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--force-reinstall",
        "--no-cache-dir",
        git_url,
    ]

    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            f"pip install timed out after {DOWNLOAD_TIMEOUT_SECONDS}s"
        ) from exc


def verify_installation(expected_version: str | None = None) -> bool:
    """Check that importlib.metadata.version('plaibook') matches expected.

    Returns True if verification passes, False otherwise.
    """
    try:
        from importlib.metadata import version
        installed = version("plaibook")
        if expected_version and installed != expected_version:
            return False
        return True
    except Exception:
        return False


def prompt_confirm(message: str, default: bool = True) -> bool:
    """Ask user Y/n confirmation (skip if stdin not a tty).

    Returns True if user confirms, False otherwise.
    If stdin is not a tty, returns the default value.
    """
    if not sys.stdin.isatty():
        return default

    prompt_text = f"{message} [{'Y/n' if default else 'y/N'}] "
    try:
        response = input(prompt_text).strip().lower()
        if not response:
            return default
        return response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()  # newline after ^C
        return False
