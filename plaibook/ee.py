# -*- coding: utf-8 -*-
"""Run one review in one execution-environment container with ansible-navigator.

The container holds the review, its checkout, and the provider key. The host
keeps ``~/.ssh``, the SSH agent, other provider keys, and the plaibook cache.
Everything the container writes is untrusted until this module has checked it.

The run's home and the reviews directory have the same path in the container
as on the host, so a path in ``last_run.<run_id>.json`` opens on the host
without translation. The playbook is mounted read-only at a path that does not
exist on the host. ansible-runner mounts a playbook's directory read-write when
that directory exists on the host, and a checkout must not be writable.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from plaibook import __version__
from plaibook.config import CONFIG_DIRNAME, VARS_NAME, vars_path
from plaibook.playbook import ANSIBLE_CFG_NAME, PLAYBOOK_NAME, ansible_tool_bin, last_run_dir, last_run_path

IMAGE_REPOSITORY = "ghcr.io/aknochow/plaibook-ee"
ENV_IMAGE = "PLAIBOOK_EE_IMAGE"
# review.yml and the image's Python are both fixed by execution-environment.yml.
IMAGE_PYTHON = "/usr/bin/python3"
RUNS_DIRNAME = "ee-runs"
RUN_LABEL = "io.plaibook.run-id"
# Container paths. They must not exist on the host. See the module docstring.
CONTAINER_PLAYBOOK = PurePosixPath("/plaibook/playbook")
CONTAINER_LIBRARY = PurePosixPath("/plaibook/lib")
# A killed plai cannot clean up, and the run tree holds ansible-runner's env file.
STALE_RUN_SECONDS = 24 * 60 * 60
MAX_RESULT_BYTES = 64 * 1024 * 1024
ENGINE_TIMEOUT_SECONDS = 60

# What the navigator process needs to find the container engine. Everything
# else is dropped: ansible-runner copies every ANSIBLE_* variable into the
# container, and it mounts the SSH agent when SSH_AUTH_SOCK is set.
_ENGINE_ENV = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "USER",
    "LOGNAME",
    "XDG_RUNTIME_DIR",
    "CONTAINER_HOST",
    "CONTAINER_CONNECTION",
    "CONTAINER_SSHKEY",
    "CONTAINERS_CONF",
    "CONTAINERS_STORAGE_CONF",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
)

_OPENAI_ENV = ("OPENAI_API_KEY", "ANSIBLE_OPENAI_API_KEY", "OPENAI_BASE_URL", "ANSIBLE_OPENAI_BASE_URL")
_XAI_ENV = ("XAI_API_KEY", "GROK_API_KEY")
_FAMILY_ENV: dict[str, tuple[str, ...]] = {
    "openai": _OPENAI_ENV,
    "xai": _XAI_ENV,
    "cursor": ("CURSOR_API_KEY",),
    "claude": (
        "ANTHROPIC_API_KEY",
        "ANSIBLE_CLAUDE_PROVIDER",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "ANTHROPIC_VERTEX_REGION",
    ),
    "gemini": (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ANSIBLE_GEMINI_BACKEND",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    ),
}
_FAMILY_PREFIX: dict[str, str] = {
    "openai": "ANSIBLE_OPENAI_",
    "xai": "ANSIBLE_XAI_",
    "cursor": "ANSIBLE_CURSOR_",
    "claude": "ANSIBLE_CLAUDE_",
    "gemini": "ANSIBLE_GEMINI_",
}
_GITHUB_ENV = ("GITHUB_TOKEN", "GH_TOKEN")
_GITLAB_ENV = ("GITLAB_TOKEN", "GLAB_TOKEN")


class EeError(RuntimeError):
    """The execution-environment run could not start or returned bad results."""


@dataclass(frozen=True)
class EeRun:
    """Host directories that belong to one container."""

    run_id: str
    root: Path
    home: Path
    runner: Path
    engine_home: Path
    passwd: Path

    @property
    def cache(self) -> Path:
        return last_run_dir(self.home)

    @property
    def tmp(self) -> Path:
        return self.cache / "tmp"

    @property
    def ledger(self) -> Path:
        return self.cache / "ledger"


def default_image() -> str:
    """The image built from the same release as this package."""
    return f"{IMAGE_REPOSITORY}:{__version__}"


def resolve_image(cli_image: str | None, env: Mapping[str, str] | None = None) -> str:
    environ = os.environ if env is None else env
    return (cli_image or "").strip() or (environ.get(ENV_IMAGE) or "").strip() or default_image()


def find_engine() -> str:
    for name in ("podman", "docker"):
        if shutil.which(name):
            return name
    raise EeError("--ee needs podman or docker on PATH. Neither was found.")


def navigator_bin() -> str:
    try:
        return ansible_tool_bin("ansible-navigator")
    except FileNotFoundError as exc:
        raise EeError("--ee needs ansible-navigator. Install it with `pipx install ansible-navigator`.") from exc


def engine_env(run: EeRun, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for navigator and the engine, with HOME moved off the real home.

    ansible-runner mounts ``$HOME/.ssh`` into every container and has no option
    to stop. HOME therefore names a directory with no ``.ssh``.
    """
    environ = os.environ if env is None else env
    kept = {name: environ[name] for name in _ENGINE_ENV if environ.get(name)}
    kept["HOME"] = str(run.engine_home)
    kept["TMPDIR"] = str(run.root / "tmp")
    return kept


def _link_engine_config(run: EeRun, env: Mapping[str, str], home: Path) -> None:
    """Keep engine connections, storage, and registry logins under the moved HOME."""
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    links = (
        (run.engine_home / ".config" / "containers", (config / "containers", home / ".config" / "containers")),
        (run.engine_home / ".local" / "share" / "containers", (data / "containers",)),
        (run.engine_home / ".docker", (Path(env["DOCKER_CONFIG"]) if env.get("DOCKER_CONFIG") else home / ".docker",)),
    )
    for link, sources in links:
        for source in sources:
            if source.exists():
                link.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                link.symlink_to(source)
                break


def prepare_run(run_id: str, *, home: Path | None = None, env: Mapping[str, str] | None = None) -> EeRun:
    """Create the private per-run tree. Remove it with :func:`remove_run`."""
    environ = os.environ if env is None else env
    real_home = Path(home) if home is not None else Path.home()
    # Resolved first: navigator mounts its working directory at the real path.
    runs_dir = last_run_dir(Path(os.path.realpath(real_home))) / RUNS_DIRNAME
    root = runs_dir / run_id
    run = EeRun(
        run_id=run_id,
        root=root,
        home=root / "home",
        runner=root / "runner",
        engine_home=root / "engine-home",
        passwd=root / "passwd",
    )
    if ":" in str(root):
        raise EeError(f"{root} contains ':', which a container volume mount cannot express.")
    try:
        runs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(runs_dir, 0o700)
        _remove_stale_runs(runs_dir)
        root.mkdir(mode=0o700)
        for path in (run.home, run.runner, run.engine_home, root / "tmp"):
            path.mkdir(mode=0o700)
        run.tmp.mkdir(mode=0o700, parents=True)
        _link_engine_config(run, environ, real_home)
        _write_passwd(run)
        _copy_operator_vars(run, environ, real_home)
    except OSError as exc:
        shutil.rmtree(root, ignore_errors=True)
        raise EeError(f"Cannot create the run directory {root}: {exc}") from exc
    return run


def _remove_stale_runs(runs_dir: Path) -> None:
    cutoff = time.time() - STALE_RUN_SECONDS
    for entry in runs_dir.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _write_passwd(run: EeRun) -> None:
    """Name this UID in the container with HOME as its account home.

    The image entrypoint prefers the password database home over $HOME and
    rewrites $HOME to match. An entry the run owns keeps results on the mount,
    and async_status reads the same home the async wrapper writes to.
    """
    uid = os.getuid()
    gid = os.getgid()
    account = f"plaibook:x:{uid}:{gid}:plaibook:{run.home}:/bin/bash\n"
    root = "" if uid == 0 else "root:x:0:0:root:/root:/bin/bash\n"
    run.passwd.write_text(root + account, encoding="utf-8")
    os.chmod(run.passwd, 0o644)


def _copy_operator_vars(run: EeRun, env: Mapping[str, str], home: Path) -> None:
    """Give the container a copy of vars.yml. It must not rewrite the host file."""
    source = vars_path(env=env, home=home)
    if not source.is_file():
        return
    target = run.home / ".config" / CONFIG_DIRNAME / VARS_NAME
    target.parent.mkdir(mode=0o700, parents=True)
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)


def remove_run(run: EeRun) -> None:
    """Delete the run tree, which holds ansible-runner's env file and its values."""
    shutil.rmtree(run.root, ignore_errors=True)


def credential_names(
    family: str | None,
    target: str | None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Names of the variables this one review may see, in a stable order."""
    environ = os.environ if env is None else env
    key = (family or "claude").strip()
    names = list(_FAMILY_ENV.get(key, ()))
    if key == "openai":
        base_url = environ.get("OPENAI_BASE_URL") or environ.get("ANSIBLE_OPENAI_BASE_URL") or ""
        has_openai_key = environ.get("OPENAI_API_KEY") or environ.get("ANSIBLE_OPENAI_API_KEY")
        if "api.x.ai" in base_url or not has_openai_key:
            names.extend(_XAI_ENV)
    prefix = _FAMILY_PREFIX.get(key)
    tunables = sorted(
        name
        for name in environ
        if name.startswith("ANSIBLE_REVIEW_") or (prefix is not None and name.startswith(prefix))
    )
    names.extend(tunables)
    target_text = (target or "").lower()
    # A self-hosted GitLab need not have "gitlab" in its host name.
    if target_text and ("gitlab" in target_text or "merge_requests" in target_text or "!" in target_text):
        names.extend(_GITLAB_ENV)
    elif target_text:
        names.extend(_GITHUB_ENV)
    else:
        names.extend(_GITHUB_ENV + _GITLAB_ENV)
    seen: dict[str, None] = {}
    for name in names:
        if environ.get(name):
            seen[name] = None
    return list(seen)


def github_token_from_cli(env: Mapping[str, str] | None = None) -> str | None:
    """The host `gh` login lives in a keyring the container cannot read."""
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        completed = subprocess.run(
            [gh, "auth", "token"],
            check=False,
            capture_output=True,
            text=True,
            timeout=ENGINE_TIMEOUT_SECONDS,
            env=dict(os.environ if env is None else env),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _is_rootless(engine: str, env: Mapping[str, str]) -> bool:
    """Ask the engine, which also proves it is reachable before any review work."""
    if engine != "podman":
        command = [engine, "version", "--format", "{{.Server.Version}}"]
    else:
        command = [engine, "info", "--format", "{{.Host.Security.Rootless}}"]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=ENGINE_TIMEOUT_SECONDS,
            env=dict(env),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EeError(f"{engine} is not usable: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-1000:]
        raise EeError(f"{engine} cannot reach its container runtime: {detail}")
    return engine == "podman" and completed.stdout.strip().lower() == "true"


def container_options(run: EeRun, engine: str, env: Mapping[str, str]) -> list[str]:
    """Options for the engine's `run`.

    SELinux labels are off so that no host directory is relabeled. ansible-runner
    adds --ipc=host, and a later --ipc wins.
    """
    options = [
        f"--user={os.getuid()}:{os.getgid()}",
        f"--label={RUN_LABEL}={run.run_id}",
        "--ipc=private",
        "--security-opt=label=disable",
        "--security-opt=no-new-privileges",
        "--cap-drop=all",
    ]
    if _is_rootless(engine, env):
        options.append("--userns=keep-id")
    return options


def playbook_mounts(playbook_root: Path, package_dir: Path) -> tuple[list[tuple[Path, str]], str, str]:
    """Read-only (host, container) mounts, the container playbook root, and PYTHONPATH.

    review.yml's plugins import `plaibook`, which the image does not contain. A
    checkout carries the package. A wheel carries the playbook inside the package.
    """
    root = playbook_root.resolve()
    package = package_dir.resolve()
    if package.is_relative_to(root):
        return [(root, str(CONTAINER_PLAYBOOK))], str(CONTAINER_PLAYBOOK), str(CONTAINER_PLAYBOOK)
    library = CONTAINER_LIBRARY / package.name
    if root.is_relative_to(package):
        return [(package, str(library))], str(library / root.relative_to(package)), str(CONTAINER_LIBRARY)
    return (
        [(root, str(CONTAINER_PLAYBOOK)), (package, str(library))],
        str(CONTAINER_PLAYBOOK),
        str(CONTAINER_LIBRARY),
    )


def build_navigator_command(
    *,
    navigator: str,
    engine: str,
    image: str,
    run: EeRun,
    playbook_root: Path,
    package_dir: Path,
    reviews_root: Path,
    extra_vars: dict,
    credentials: list[str],
    options: list[str],
    extra_env: Mapping[str, str] | None = None,
    verbosity: int = 0,
) -> list[str]:
    read_only, container_root, python_path = playbook_mounts(playbook_root, package_dir)
    for path in (*(host for host, _ in read_only), reviews_root):
        if ":" in str(path) or "," in str(path):
            raise EeError(f"{path} contains ':' or ',', which a container volume mount cannot express.")
    set_env = {
        "ANSIBLE_CONFIG": f"{container_root}/{ANSIBLE_CFG_NAME}",
        # ansible-runner replaces ansible.cfg's callback_plugins with its own
        # directory unless this names ours.
        "ANSIBLE_CALLBACK_PLUGINS": f"{container_root}/callback_plugins",
        # plai's own fix for this does not run inside the container.
        "ANSIBLE_ASYNC_DIR": str(run.home / ".ansible_async"),
        "PYTHONPATH": python_path,
        "PYTHONDONTWRITEBYTECODE": "1",
        **(extra_env or {}),
    }
    command = [
        navigator,
        "run",
        f"{container_root}/{PLAYBOOK_NAME}",
        "--mode",
        "stdout",
        "--ee",
        "true",
        "--ce",
        engine,
        "--eei",
        image,
        "--pull-policy",
        "missing",
        # A playbook artifact is the whole transcript. The summary is the result.
        "--pae",
        "false",
        "--dc",
        "false",
        "--lf",
        str(run.root / "navigator.log"),
        "--rad",
        str(run.runner),
    ]
    for name, value in set_env.items():
        command.extend(["--senv", f"{name}={value}"])
    for name in credentials:
        command.extend(["--penv", name])
    # --co, not --eev: navigator resolves an --eev destination against the host
    # filesystem, and on macOS that turns /etc/passwd into /private/etc/passwd.
    volumes = [f"{run.passwd}:/etc/passwd:ro"]
    volumes.extend(f"{host}:{container}:ro" for host, container in read_only)
    volumes.append(f"{reviews_root}:{reviews_root}:rw")
    # --co, not --senv: ansible-runner also gives --senv values to the engine
    # client on the host, which needs its own HOME to find its connection.
    container_env = [f"--env=HOME={run.home}", f"--env=TMPDIR={run.tmp}"]
    for option in (*options, *container_env, *(f"--volume={volume}" for volume in volumes)):
        command.append(f"--co={option}")
    if verbosity > 0:
        command.append("-" + ("v" * min(int(verbosity), 4)))
    command.extend(["-e", json.dumps(extra_vars, separators=(",", ":"))])
    return command


def stop_run_containers(engine: str, run_id: str, env: Mapping[str, str]) -> None:
    """Killing navigator can leave its container running with the key inside."""
    try:
        listed = subprocess.run(
            [engine, "ps", "-aq", "--filter", f"label={RUN_LABEL}={run_id}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=ENGINE_TIMEOUT_SECONDS,
            env=dict(env),
        )
        for container in listed.stdout.split():
            subprocess.run(
                [engine, "rm", "-f", container],
                check=False,
                capture_output=True,
                text=True,
                timeout=ENGINE_TIMEOUT_SECONDS,
                env=dict(env),
            )
    except (OSError, subprocess.SubprocessError):
        pass


def _read_container_file(path: Path, confined_to: Path) -> bytes | None:
    """Read a file the container wrote, refusing links out of its own tree."""
    try:
        resolved = Path(os.path.realpath(path))
    except OSError:
        return None
    if not resolved.is_relative_to(Path(os.path.realpath(confined_to))):
        raise EeError(f"{path} leaves the run directory. The container result was not used.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise EeError(f"Cannot read container result {path}: {exc}") from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise EeError(f"{path} is not a regular file. The container result was not used.")
        if info.st_size > MAX_RESULT_BYTES:
            raise EeError(f"{path} is larger than {MAX_RESULT_BYTES} bytes. The container result was not used.")
        return handle.read(MAX_RESULT_BYTES + 1)


def _check_target_paths(document: dict, allowed: Path) -> None:
    """`plai` opens the summary file a target names. Keep that under the reviews tree."""
    root = Path(os.path.realpath(allowed))
    for target in document.get("targets") or []:
        if not isinstance(target, dict):
            continue
        for key, value in target.items():
            if not key.endswith("_path") or not value:
                continue
            if not Path(os.path.realpath(str(value))).is_relative_to(root):
                raise EeError(f"Target {key} {value} is outside {root}. The container result was not used.")


def publish_results(
    run: EeRun,
    *,
    reviews_root: Path,
    ledger_dir: Path,
    ledger_name: str,
    home: Path | None = None,
) -> Path | None:
    """Copy this run's last_run file and ledger lines out of the container's home.

    Returns the host last_run path, or None when the playbook never wrote one.
    """
    source = run.cache / f"last_run.{run.run_id}.json"
    payload = _read_container_file(source, run.home)
    _append_ledger(run, ledger_dir=ledger_dir, ledger_name=ledger_name)
    if payload is None:
        return None
    try:
        document = json.loads(payload)
    except ValueError as exc:
        raise EeError(f"{source} is not JSON. The container result was not used.") from exc
    if not isinstance(document, dict):
        raise EeError(f"{source} is not a JSON object. The container result was not used.")
    _check_target_paths(document, reviews_root)
    target = last_run_path(run.run_id, home)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, target)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def _append_ledger(run: EeRun, *, ledger_dir: Path, ledger_name: str) -> None:
    payload = _read_container_file(run.ledger / f"{ledger_name}.jsonl", run.home)
    if not payload:
        return
    lines: list[bytes] = []
    for raw in payload.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("run_id") == run.run_id:
            lines.append(json.dumps(entry, separators=(",", ":")).encode("utf-8") + b"\n")
    if not lines:
        return
    ledger_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(ledger_dir / f"{ledger_name}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "ab") as handle:
        handle.write(b"".join(lines))


def ledger_host_name() -> str:
    """The container hostname is random. The ledger file is named for the host."""
    name = socket.gethostname().split(".")[0].lower()
    cleaned = "".join(char if char.isalnum() or char in "_.-" else "-" for char in name)
    return cleaned or "localhost"
