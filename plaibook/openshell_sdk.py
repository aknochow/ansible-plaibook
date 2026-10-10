# -*- coding: utf-8 -*-
"""Install the OpenShell SDK into an interpreter that can import it.

``openshell>=0.1.3`` requires Python 3.11. Ansible runs controller
modules, including ``aknochow.openshell.sandbox``, with the same
interpreter as ``ansible-playbook``. A Python 3.10 ``plai`` therefore
prepares ``~/.cache/ansible-plaibook/sandbox-runtime`` from the first
Python 3.11+ it can find and re-execs that runtime's ``plai``. The
3.10 site-packages are not modified.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO
from urllib.parse import unquote, urlparse

from plaibook.collections import redact_git_userinfo
from plaibook.pip_hashed import lock_digest, pip_install_hashed_argv
from plaibook.playbook import last_run_dir
from plaibook.runtime import interpreter_is_externally_managed

# Matches aknochow.openshell (OPENSHELL_SDK_SPEC) and the 0.1 gateway
# started by the upstream installer. 0.0.116 encodes CreateSandboxRequest
# fields that gateway rejects. 0.1.3 is current PyPI; <0.2 is the ceiling.
SDK_SPEC = "openshell>=0.1.3,<0.2"
HASHED_REQUIREMENTS = "openshell-requirements.txt"
RUNTIME_HASHED_REQUIREMENTS = "sandbox-runtime-requirements.txt"
BUILD_BACKEND_REQUIREMENTS = "build-backend-requirements.txt"
SDK_MIN_PYTHON = (3, 11)
RUNTIME_DIRNAME = "sandbox-runtime"
STAMP_NAME = "sandbox-runtime.json"
LOCK_NAME = "sandbox-runtime.lock"
ENV_REEXEC = "PLAIBOOK_SANDBOX_RUNTIME"
_MIN = (0, 1, 3)
_MAX = (0, 2, 0)
_RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\+.*)?$")
_BREW_BIN = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
_BREW_OPT = (Path("/opt/homebrew/opt"), Path("/usr/local/opt"))
_SDK_MINORS = range(11, 15)


class OpenshellSdkError(RuntimeError):
    """Could not put a compatible openshell SDK on a 3.11+ interpreter."""


def release_tuple(version: str) -> tuple[int, int, int] | None:
    """Return (major, minor, patch) for a final release. Pre-releases are None."""
    match = _RELEASE.fullmatch(version.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def version_satisfies(version: str) -> bool:
    parsed = release_tuple(version)
    return parsed is not None and _MIN <= parsed < _MAX


def sdk_satisfies() -> bool:
    """True when this process can import SandboxClient from an in-range SDK."""
    try:
        from openshell import SandboxClient
    except ImportError:
        return False
    if SandboxClient is None:
        return False
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("openshell")
    except PackageNotFoundError:
        return False
    return version_satisfies(installed)


def interpreter_version(python: str) -> tuple[int, int, int]:
    """Return (major, minor, patch) for ``python``."""
    if _same_executable(python):
        return (sys.version_info[0], sys.version_info[1], sys.version_info[2])
    completed = _run(
        [python, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
        timeout=30,
    )
    if completed.returncode != 0:
        detail = _detail(completed) or completed.returncode
        raise OpenshellSdkError(f"cannot read Python version of {python}: {detail}")
    parts = completed.stdout.split()
    if len(parts) != 1:
        raise OpenshellSdkError(f"cannot read Python version of {python}: {completed.stdout!r}")
    nums = parts[0].split(".")
    if len(nums) != 3 or not all(part.isdigit() for part in nums):
        raise OpenshellSdkError(f"cannot read Python version of {python}: {completed.stdout!r}")
    return (int(nums[0]), int(nums[1]), int(nums[2]))


def interpreter_supports_sdk(python: str) -> bool:
    """True when ``python`` is new enough for ``SDK_SPEC``."""
    major, minor, _patch = interpreter_version(python)
    return (major, minor) >= SDK_MIN_PYTHON


def find_sdk_python() -> str | None:
    """First Python >= 3.11 on PATH or in a Homebrew prefix.

    3.11 is preferred over newer interpreters so the runtime stays on
    the minimum the SDK documents.
    """
    candidates: list[str] = []
    for minor in _SDK_MINORS:
        found = shutil.which(f"python3.{minor}")
        if found:
            candidates.append(found)
        for root in _BREW_BIN:
            brew = root / f"python3.{minor}"
            if brew.is_file():
                candidates.append(str(brew))
        for opt in _BREW_OPT:
            brew = opt / f"python@3.{minor}" / "bin" / f"python3.{minor}"
            if brew.is_file():
                candidates.append(str(brew))
    found3 = shutil.which("python3")
    if found3:
        candidates.append(found3)
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(Path(candidate).resolve())
        except OSError:
            key = candidate
        if key in seen:
            continue
        seen.add(key)
        if _same_executable(candidate) and sys.version_info < SDK_MIN_PYTHON:
            continue
        try:
            if interpreter_supports_sdk(candidate):
                return candidate
        except OpenshellSdkError:
            continue
    return None


def spec_from_direct_url(data: dict, version: str | None = None) -> str:
    """Pip requirement that reinstalls the plaibook build now running.

    Never returns ``plaibook==<version>``: that would let pip fetch an
    unhashed wheel from an index. Git commits and local paths only.
    """
    _ = version
    url = str(data.get("url") or "")
    vcs = data.get("vcs_info") or {}
    commit = str(vcs.get("commit_id") or "")
    if vcs.get("vcs") == "git" and commit and url:
        if _cleartext_http_url(url):
            raise OpenshellSdkError(
                "plaibook was installed from git over HTTP. Reinstall from HTTPS or a local path."
            )
        prefix = url if url.startswith("git+") else f"git+{url}"
        return f"{redact_git_userinfo(prefix)}@{commit}"
    if url.startswith("file:"):
        path = unquote(urlparse(url).path)
        if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        if path:
            return path
    raise OpenshellSdkError(
        "Cannot tell which plaibook build is running (no git commit or local path in direct_url.json). "
        "Reinstall from git or a local checkout; a PyPI version pin is not used for the sandbox runtime."
    )


def _cleartext_http_url(url: str) -> bool:
    rest = url.strip().removeprefix("git+").removeprefix("GIT+")
    return rest.lower().startswith("http://")


def _checkout_source_path() -> str | None:
    root = Path(__file__).resolve().parent.parent
    pyproject = root / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    if 'name = "plaibook"' in text:
        return str(root)
    return None


_INSTALLED_SRC_NAME = "pypi-src"
_INSTALLED_PYPROJECT = """\
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "plaibook"
version = "%s"
requires-python = ">=3.10"

[project.scripts]
plaibook = "plaibook.cli:main"
plai = "plaibook.cli:main"

[tool.setuptools.packages.find]
include = ["plaibook*"]

[tool.setuptools.package-data]
plaibook = ["share/**/*", "hashed/**/*"]
"""


def _direct_url_is_local_or_git(data: dict) -> bool:
    url = str(data.get("url") or "")
    vcs = data.get("vcs_info") or {}
    if vcs.get("vcs") == "git" and vcs.get("commit_id") and url:
        return True
    return url.startswith("file:")


def _materialize_installed_source(home: Path | None = None) -> str | None:
    """Wheelable source tree from the plaibook files already on this interpreter.

    Index-installed wheels have no checkout ``pyproject.toml`` and usually no
    PEP 610 ``direct_url.json``. Copy the installed package (including vendored
    ``share/``) and write a setuptools config that does not run the checkout
    ``build_py`` hook, so ``pip wheel --no-build-isolation`` can rebuild this
    same version into the Python 3.11 sandbox runtime.
    """
    pkg = Path(__file__).resolve().parent
    if not (pkg / "__init__.py").is_file():
        return None
    dest = last_run_dir(home) / _INSTALLED_SRC_NAME
    try:
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / "plaibook"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            pkg,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
            symlinks=False,
        )
        from plaibook import __version__

        (dest / "pyproject.toml").write_text(_INSTALLED_PYPROJECT % __version__, encoding="utf-8")
    except OSError:
        return None
    if 'name = "plaibook"' not in (dest / "pyproject.toml").read_text(encoding="utf-8"):
        return None
    return str(dest)


def plaibook_install_spec(*, home: Path | None = None) -> str:
    """Requirement for the plaibook distribution of this process."""
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        dist = distribution("plaibook")
    except PackageNotFoundError as exc:
        raise OpenshellSdkError(
            "plaibook is not installed as a distribution, so a Python 3.11 sandbox runtime cannot be created."
        ) from exc
    raw = dist.read_text("direct_url.json")
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenshellSdkError("plaibook direct_url.json is not valid JSON.") from exc
        if not isinstance(data, dict):
            raise OpenshellSdkError("plaibook direct_url.json is not an object.")
        if _direct_url_is_local_or_git(data):
            return spec_from_direct_url(data, dist.version)
    local = _checkout_source_path()
    if local:
        return local
    installed = _materialize_installed_source(home)
    if installed:
        return installed
    raise OpenshellSdkError(
        "Cannot tell which plaibook build is running (no direct_url.json and no local checkout). "
        "Reinstall from git or a local path; pip will not fetch plaibook==%s from an index."
        % dist.version
    )


def ensure_openshell_sdk(
    python: str | None = None,
    *,
    stderr: TextIO | None = None,
) -> None:
    """pip-install the pinned SDK when this interpreter can run it.

    Refuses Python older than 3.11 without calling pip. An existing
    copy is uninstalled only after ``pip install --dry-run`` resolves
    ``SDK_SPEC``, so a failed resolve cannot delete the package.
    """
    exe = python or sys.executable
    if not interpreter_supports_sdk(exe):
        version = interpreter_version(exe)
        raise OpenshellSdkError(
            f"{exe} is Python {version[0]}.{version[1]}. "
            f"OpenShell sandboxes need Python {SDK_MIN_PYTHON[0]}.{SDK_MIN_PYTHON[1]}+ "
            f"because {SDK_SPEC} does not publish wheels for this interpreter. "
            "Install Python 3.11 and re-run plai review (it prepares "
            "~/.cache/ansible-plaibook/sandbox-runtime), or pass --no-sandbox."
        )
    if _interpreter_satisfies(exe):
        return
    if interpreter_is_externally_managed(exe):
        raise OpenshellSdkError(
            f"OpenShell SDK ({SDK_SPEC}) is not installed for {exe}. "
            "Reinstall plaibook with pipx (or into a virtualenv). "
            "Pass --no-sandbox to review without a sandbox."
        )
    out = stderr if stderr is not None else sys.stderr
    try:
        dry_argv = pip_install_hashed_argv(exe, HASHED_REQUIREMENTS, dry_run=True)
        install_argv = pip_install_hashed_argv(exe, HASHED_REQUIREMENTS)
    except OSError as exc:
        raise OpenshellSdkError(f"cannot read hashed OpenShell requirements: {exc}") from exc
    if _package_present(exe):
        dry = _run(dry_argv, timeout=180)
        if dry.returncode != 0:
            detail = _detail(dry) or dry.returncode
            raise OpenshellSdkError(
                f"pip install --dry-run --require-hashes -r {HASHED_REQUIREMENTS} failed for {exe}: {detail}. "
                "The existing openshell was left in place."
            )
        uninstall = _run([exe, "-m", "pip", "uninstall", "-y", "--disable-pip-version-check", "openshell"], timeout=120)
        if uninstall.returncode != 0:
            detail = _detail(uninstall) or uninstall.returncode
            raise OpenshellSdkError(f"pip uninstall openshell failed: {detail}")
    out.write(f"Installing OpenShell SDK ({SDK_SPEC}) for {exe}…\n")
    out.flush()
    install = _run(install_argv, timeout=300)
    if install.returncode != 0:
        detail = _detail(install) or install.returncode
        raise OpenshellSdkError(
            f"pip install --require-hashes -r {HASHED_REQUIREMENTS} failed: {detail}"
        )
    if not _interpreter_satisfies(exe):
        raise OpenshellSdkError(
            f"pip install --require-hashes -r {HASHED_REQUIREMENTS} finished but {exe} still cannot import "
            "openshell.SandboxClient from that range."
        )


def prepare_sandbox_runtime(*, stderr: TextIO | None = None, home: Path | None = None) -> str:
    """Return a Python >= 3.11 whose site-packages has this plaibook and the SDK.

    The runtime lives under ``~/.cache/ansible-plaibook/sandbox-runtime``.
    It is recreated when the base interpreter or the plaibook build changes.
    """
    out = stderr if stderr is not None else sys.stderr
    base = find_sdk_python()
    if not base:
        version = f"{sys.version_info[0]}.{sys.version_info[1]}"
        raise OpenshellSdkError(
            f"{sys.executable} is Python {version}. OpenShell sandboxes need Python 3.11+ "
            f"because {SDK_SPEC} does not install on 3.10. "
            "Install Python 3.11 (Homebrew: brew install python@3.11) and re-run the same "
            "plai command. plai will create ~/.cache/ansible-plaibook/sandbox-runtime from it. "
            "Or pass --no-sandbox."
        )
    with _exclusive_runtime_lock(home):
        spec = plaibook_install_spec(home=home)
        return _prepare_sandbox_runtime_locked(base, spec, out, home)


def _prepare_sandbox_runtime_locked(
    base: str,
    spec: str,
    out: TextIO,
    home: Path | None,
) -> str:
    runtime, stamp_path = _runtime_paths(home)
    _refuse_symlink(runtime)
    _refuse_symlink(stamp_path)
    python = _venv_python(runtime)
    plai = _venv_plai(runtime)
    base_key = _resolved(base)
    base_version = ".".join(str(part) for part in interpreter_version(base))
    stamp = _read_stamp(stamp_path)
    lock_id = _runtime_lock_id()
    source = _source_fingerprint(spec)
    if (
        python.is_file()
        and plai.is_file()
        and stamp.get("base") == base_key
        and stamp.get("base_version") == base_version
        and stamp.get("spec") == spec
        and stamp.get("locks") == lock_id
        and stamp.get("source", "") == source
    ):
        ensure_openshell_sdk(str(python), stderr=out)
        return str(python)
    if runtime.exists() and not runtime.is_dir():
        raise OpenshellSdkError(f"{runtime} exists and is not a directory. Remove it so plaibook can own this runtime.")
    from plaibook import __version__

    out.write(
        f"plaibook {__version__}: OpenShell sandboxes need Python 3.11+. "
        f"Preparing {runtime} from {base}…\n"
    )
    out.flush()
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    created = _run([base, "-m", "venv", str(runtime)], timeout=180)
    if created.returncode != 0:
        detail = _detail(created) or created.returncode
        raise OpenshellSdkError(f"could not create {runtime} with {base}: {detail}")
    # Hashed setuptools/wheel first, then pip wheel --no-build-isolation so
    # the build backend is not resolved from an unhashed index.
    _pip_install_hashed(str(python), BUILD_BACKEND_REQUIREMENTS, timeout=300)
    _install_plaibook_hashed(str(python), spec)
    _pip_install_hashed(str(python), RUNTIME_HASHED_REQUIREMENTS, timeout=600)
    _pip_install_hashed(str(python), HASHED_REQUIREMENTS, timeout=600)
    if not plai.is_file():
        raise OpenshellSdkError(f"{plai} was not created by the runtime install.")
    _write_stamp(
        stamp_path,
        {
            "base": base_key,
            "base_version": base_version,
            "spec": spec,
            "locks": lock_id,
            "source": source,
        },
    )
    ensure_openshell_sdk(str(python), stderr=out)
    return str(python)


def reexec_sandbox_runtime(
    *,
    stderr: TextIO | None = None,
    argv: list[str] | None = None,
    home: Path | None = None,
) -> None:
    """Stay on this interpreter when it can run the SDK; otherwise re-exec.

    On success with a switch, this function does not return.
    """
    out = stderr if stderr is not None else sys.stderr
    if interpreter_supports_sdk(sys.executable):
        ensure_openshell_sdk(stderr=out)
        return
    if os.environ.get(ENV_REEXEC) == "1":
        raise OpenshellSdkError(
            f"Refusing to switch interpreters twice. {sys.executable} still cannot run the OpenShell SDK."
        )
    runtime_python = prepare_sandbox_runtime(stderr=out, home=home)
    plai = Path(runtime_python).with_name("plai.exe" if os.name == "nt" else "plai")
    if not plai.is_file():
        raise OpenshellSdkError(f"{plai} is missing from the sandbox runtime.")
    out.write(f"Continuing this sandboxed review with {plai}.\n")
    out.flush()
    os.environ[ENV_REEXEC] = "1"
    os.environ.pop("PYTHONPATH", None)
    venv_root = str(Path(runtime_python).resolve().parents[1])
    current_venv = os.environ.get("VIRTUAL_ENV")
    if current_venv:
        try:
            same = Path(current_venv).resolve() == Path(venv_root).resolve()
        except OSError:
            same = False
        if not same:
            os.environ.pop("VIRTUAL_ENV", None)
    os.environ["VIRTUAL_ENV"] = venv_root
    args = [str(plai), *(sys.argv[1:] if argv is None else argv)]
    os.execv(str(plai), args)


def _interpreter_satisfies(python: str) -> bool:
    if _same_executable(python):
        return sdk_satisfies()
    probe = "import sys\nfrom plaibook.openshell_sdk import sdk_satisfies\nsys.exit(0 if sdk_satisfies() else 1)\n"
    completed = _run([python, "-c", probe], timeout=60)
    return completed.returncode == 0


def _package_present(python: str) -> bool:
    if _same_executable(python):
        try:
            from importlib.metadata import PackageNotFoundError, version
        except ImportError:
            return False
        try:
            version("openshell")
        except PackageNotFoundError:
            return False
        return True
    probe = (
        "import importlib.metadata, sys\n"
        "try:\n"
        "    importlib.metadata.version('openshell')\n"
        "except importlib.metadata.PackageNotFoundError:\n"
        "    sys.exit(1)\n"
    )
    completed = _run([python, "-c", probe], timeout=60)
    return completed.returncode == 0


def _same_executable(python: str) -> bool:
    try:
        return Path(python).resolve() == Path(sys.executable).resolve()
    except OSError:
        return python == sys.executable


def _resolved(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return path


def _runtime_paths(home: Path | None) -> tuple[Path, Path]:
    root = last_run_dir(home)
    return root / RUNTIME_DIRNAME, root / STAMP_NAME


@contextmanager
def _exclusive_runtime_lock(home: Path | None = None) -> Iterator[None]:
    """Serialize sandbox-runtime create/replace across concurrent plai processes.

    POSIX ``fcntl.flock`` only. Windows is not a supported plaibook host.
    """
    lock_path = last_run_dir(home) / LOCK_NAME
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        raise OpenshellSdkError(f"Cannot open sandbox-runtime lock {lock_path}: {exc}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            raise OpenshellSdkError(f"Cannot lock sandbox runtime {lock_path}: {exc}") from exc
        yield
    finally:
        os.close(fd)


def _venv_python(runtime: Path) -> Path:
    if os.name == "nt":
        return runtime / "Scripts" / "python.exe"
    return runtime / "bin" / "python"


def _venv_plai(runtime: Path) -> Path:
    if os.name == "nt":
        return runtime / "Scripts" / "plai.exe"
    return runtime / "bin" / "plai"


def _refuse_symlink(path: Path) -> None:
    if path.is_symlink():
        raise OpenshellSdkError(
            f"{path} is a symlink; plaibook will not use it as the OpenShell runtime. Remove the symlink."
        )


def _read_stamp(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_stamp(path: Path, payload: dict) -> None:
    _refuse_symlink(path)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _runtime_lock_id() -> str:
    return lock_digest(BUILD_BACKEND_REQUIREMENTS, RUNTIME_HASHED_REQUIREMENTS, HASHED_REQUIREMENTS)


_SOURCE_SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "__pycache__", "build", "dist", ".pytest_cache", "share"}
)


def _source_fingerprint(spec: str) -> str:
    """Content hash of a local checkout used as the sandbox-runtime spec.

    Git/URL specs are unchanged across source edits; a directory spec is
    the checkout path, so the stamp must include file contents or an
    editable install reuses a stale wheel.
    """
    root = Path(spec)
    try:
        if not root.is_dir():
            return ""
    except OSError:
        return ""
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir.parts[:2] == ("plaibook", "share"):
            dirnames[:] = []
            continue
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _SOURCE_SKIP_DIRS and not name.endswith(".egg-info")
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix().encode()
            try:
                payload = path.read_bytes()
            except OSError:
                continue
            digest.update(rel)
            digest.update(b"\0")
            digest.update(payload)
            digest.update(b"\0")
    return digest.hexdigest()


def _install_plaibook_hashed(venv_python: str, spec: str) -> None:
    """Wheel this plaibook build with the venv's hashed setuptools, then install it.

    ``pip wheel`` uses ``--no-build-isolation`` so the build backend is the
    hashed setuptools/wheel already in the venv, not an unhashed index resolve.
    """
    with tempfile.TemporaryDirectory(prefix="plaibook-wheel-") as tmp:
        tmp_path = Path(tmp)
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        built = _run(
            [
                venv_python,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--disable-pip-version-check",
                "-w",
                str(wheels),
                spec,
            ],
            timeout=600,
        )
        if built.returncode != 0:
            detail = _detail(built) or built.returncode
            raise OpenshellSdkError(f"pip wheel --no-deps of {spec} failed: {detail}")
        found = sorted(wheels.glob("plaibook-*.whl"))
        if len(found) != 1:
            names = ", ".join(path.name for path in found) or "none"
            raise OpenshellSdkError(
                f"pip wheel of {spec} did not produce exactly one plaibook wheel ({names})."
            )
        wheel = found[0]
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        req = tmp_path / "plaibook-wheel-requirements.txt"
        req.write_text(
            f"plaibook @ {wheel.resolve().as_uri()} --hash=sha256:{digest}\n",
            encoding="utf-8",
        )
        installed = _run(
            [
                venv_python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--require-hashes",
                "-r",
                str(req),
            ],
            timeout=600,
        )
        if installed.returncode != 0:
            detail = _detail(installed) or installed.returncode
            raise OpenshellSdkError(
                f"pip install --require-hashes of the local plaibook wheel failed: {detail}"
            )


def _pip_install_hashed(python: str, filename: str, *, timeout: int) -> None:
    try:
        argv = pip_install_hashed_argv(python, filename)
    except OSError as exc:
        raise OpenshellSdkError(f"cannot read hashed requirements {filename}: {exc}") from exc
    completed = _run(argv, timeout=timeout)
    if completed.returncode != 0:
        detail = _detail(completed) or completed.returncode
        raise OpenshellSdkError(f"pip install --require-hashes -r {filename} failed: {detail}")


def _detail(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stderr or completed.stdout or "").strip()
    if len(text) > 2000:
        text = text[-2000:]
    return text


def _run(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpenshellSdkError(f"command timed out after {timeout}s: {' '.join(argv[:4])}") from exc
    except OSError as exc:
        raise OpenshellSdkError(f"cannot run {argv[0]}: {exc}") from exc
