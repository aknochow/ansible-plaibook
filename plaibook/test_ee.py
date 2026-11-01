# -*- coding: utf-8 -*-
"""`plai review --ee`: what reaches the container and what comes back out of it."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from plaibook import __version__, ee
from plaibook.cli import build_parser, cmd_review
from plaibook.playbook import PlaybookTimeoutError, last_run_path

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET = "unit-test-secret-value"


def _run_dirs(tmp_path: Path, run_id: str = "abcDEF0123456789") -> ee.EeRun:
    return ee.prepare_run(run_id, home=tmp_path, env={})


def _command(run: ee.EeRun, tmp_path: Path, **overrides) -> list[str]:
    arguments = {
        "navigator": "ansible-navigator",
        "engine": "podman",
        "image": "example.com/ee@sha256:abc",
        "run": run,
        "playbook_root": REPO_ROOT,
        "package_dir": REPO_ROOT / "plaibook",
        "reviews_root": tmp_path / "reviews",
        "extra_vars": {"last_run_id": run.run_id},
        "credentials": ["OPENAI_API_KEY"],
        "options": ["--user=1:1"],
    }
    arguments.update(overrides)
    return ee.build_navigator_command(**arguments)


def test_operator_vars_copy_drops_secret_named_settings(tmp_path):
    config = tmp_path / "cfg"
    source = config / "ansible-plaibook" / "vars.yml"
    source.parent.mkdir(parents=True)
    source.write_text(
        "agent_family: openai\nreview_openai_max_tokens: 5\nmy_api_key: x\nprivate_key: x\nbearer_value: x\ngithub_token: x\nreview_secret: x\n"
        "nested: {keep: 1, password: x, items: [{keep: 2, api_key: x}]}\n",
        encoding="utf-8",
    )
    run = ee.prepare_run("abcDEF0123456789", home=tmp_path, env={"XDG_CONFIG_HOME": str(config)})
    copied = run.home / ".config" / "ansible-plaibook" / "vars.yml"
    assert yaml.safe_load(copied.read_text(encoding="utf-8")) == {
        "agent_family": "openai",
        "review_openai_max_tokens": 5,
        "nested": {"keep": 1, "items": [{"keep": 2}]},
    }
    assert (copied.stat().st_mode & 0o777) == 0o600


def _fake_cosign(monkeypatch, returncode=0, stdout=None, stderr=""):
    digest = "sha256:" + "a" * 64
    if stdout is None:
        stdout = json.dumps([{"critical": {"image": {"docker-manifest-digest": digest}}}])
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr("plaibook.ee.shutil.which", lambda name, path=None: f"/bin/{name}")
    monkeypatch.setattr("plaibook.ee.subprocess.run", fake_run)
    return digest, seen


def test_release_image_runs_by_the_digest_cosign_verified(monkeypatch):
    digest, seen = _fake_cosign(monkeypatch)
    image = f"ghcr.io/aknochow/plaibook-ee:{__version__}"
    verified = ee.verify_release_image(image, {"PATH": "/bin", "OPENAI_API_KEY": SECRET})
    assert verified == f"ghcr.io/aknochow/plaibook-ee@{digest}"
    assert f"{ee.SIGNING_WORKFLOW}@refs/tags/v{__version__}" in seen["command"]
    assert ee.SIGNING_ISSUER in seen["command"]
    assert SECRET not in seen["env"].values()


@pytest.mark.parametrize(
    "arguments",
    [
        {"returncode": 1, "stderr": "no matching signatures"},
        {"stdout": "not json"},
        {"stdout": "[]"},
        {"stdout": json.dumps([{"critical": {"image": {"docker-manifest-digest": "sha256:short"}}}])},
    ],
)
def test_release_image_is_refused_when_verification_fails(monkeypatch, arguments):
    _fake_cosign(monkeypatch, **arguments)
    with pytest.raises(ee.EeError):
        ee.verify_release_image("example.com/ee:1", {"PATH": "/bin"})


def test_release_image_is_refused_without_cosign(monkeypatch):
    monkeypatch.setattr("plaibook.ee.shutil.which", lambda name, path=None: None)
    with pytest.raises(ee.EeError, match="--ee-image"):
        ee.verify_release_image("example.com/ee:1", {"PATH": "/bin"})


def test_stage_checkout_copies_only_tracked_files(tmp_path):
    root = tmp_path / "checkout"
    (root / "roles").mkdir(parents=True)
    (root / "review.yml").write_text("tracked\n", encoding="utf-8")
    (root / "roles" / "gone.yml").write_text("deleted\n", encoding="utf-8")
    (root / "roles" / "link.yml").symlink_to("../review.yml")
    (root / ".gitignore").write_text("host_vars/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    (root / "roles" / "gone.yml").unlink()
    (root / "untracked.txt").write_text("private\n", encoding="utf-8")
    (root / "host_vars").mkdir()
    (root / "host_vars" / "localhost.yml").write_text("private\n", encoding="utf-8")
    run = _run_dirs(tmp_path)
    stage = ee.stage_checkout(root, run)
    assert stage == run.root / "playbook"
    assert sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if not p.is_dir()) == [
        ".gitignore",
        "review.yml",
        "roles/link.yml",
    ]
    assert (stage / "roles" / "link.yml").is_symlink()


def test_stage_checkout_leaves_a_non_checkout_alone(tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    assert ee.stage_checkout(root, _run_dirs(tmp_path)) == root.resolve()


def test_home_reaches_the_container_but_not_the_engine_client(tmp_path):
    command = _command(_run_dirs(tmp_path), tmp_path)
    senv = [command[i + 1] for i, part in enumerate(command) if part == "--senv"]
    assert not [value for value in senv if value.startswith(("HOME=", "TMPDIR="))]
    assert any(part.startswith("--co=--env=HOME=") for part in command)
    assert any(part.startswith("--co=--env=TMPDIR=") for part in command)


def test_default_image_is_this_release(monkeypatch):
    monkeypatch.delenv(ee.ENV_IMAGE, raising=False)
    assert ee.resolve_image(None) == f"ghcr.io/aknochow/plaibook-ee:{__version__}"
    assert ee.resolve_image(None, {ee.ENV_IMAGE: "env/image"}) == "env/image"
    assert ee.resolve_image("cli/image", {ee.ENV_IMAGE: "env/image"}) == "cli/image"


def test_only_this_providers_key_and_this_forges_token_are_named():
    env = {
        "OPENAI_API_KEY": SECRET,
        "XAI_API_KEY": SECRET,
        "GROK_API_KEY": SECRET,
        "ANTHROPIC_API_KEY": SECRET,
        "CURSOR_API_KEY": SECRET,
        "GH_TOKEN": SECRET,
        "GITLAB_TOKEN": SECRET,
        "AWS_SECRET_ACCESS_KEY": SECRET,
        "ANSIBLE_REVIEW_OPENAI_MODEL": "gpt-6-luna",
        "ANSIBLE_CURSOR_THING": "x",
    }
    assert ee.credential_names("openai", "org/repo/1", env) == [
        "OPENAI_API_KEY",
        "ANSIBLE_REVIEW_OPENAI_MODEL",
        "GH_TOKEN",
    ]
    assert "GITLAB_TOKEN" in ee.credential_names("openai", "gitlab:org/repo/1", env)
    assert "GH_TOKEN" not in ee.credential_names("openai", "gitlab:org/repo/1", env)
    for target in ("https://git.example.com/org/repo/-/merge_requests/1", "git.example.com:org/repo!1"):
        names = ee.credential_names("openai", target, env)
        assert "GITLAB_TOKEN" in names
        assert "GH_TOKEN" not in names
    assert {"GH_TOKEN", "GITLAB_TOKEN"} <= set(ee.credential_names("openai", None, env))


def test_openai_family_gets_the_xai_key_only_when_it_is_the_key_in_use():
    assert "XAI_API_KEY" in ee.credential_names("openai", "o/r/1", {"XAI_API_KEY": SECRET})
    url = {"OPENAI_API_KEY": SECRET, "XAI_API_KEY": SECRET, "OPENAI_BASE_URL": "https://api.x.ai/v1"}
    assert "XAI_API_KEY" in ee.credential_names("openai", "o/r/1", url)


def test_navigator_environment_drops_ssh_agent_and_ansible_settings(tmp_path):
    run = _run_dirs(tmp_path)
    kept = ee.engine_env(
        run,
        {
            "PATH": "/bin",
            "SSH_AUTH_SOCK": "/agent",
            "ANSIBLE_COLLECTIONS_PATH": "/host/collections",
            "OPENAI_API_KEY": SECRET,
            "CONTAINER_HOST": "ssh://example.com",
            "XDG_CONFIG_HOME": "/elsewhere",
        },
    )
    assert kept == {
        "PATH": "/bin",
        "CONTAINER_HOST": "ssh://example.com",
        "HOME": str(run.engine_home),
        "TMPDIR": str(run.root / "tmp"),
    }


def test_run_tree_is_private_and_moved_home_has_no_ssh(tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".config" / "containers").mkdir(parents=True)
    run = _run_dirs(tmp_path)
    assert stat.S_IMODE(run.root.stat().st_mode) == 0o700
    assert not (run.engine_home / ".ssh").exists()
    assert (run.engine_home / ".config" / "containers").resolve() == (tmp_path / ".config" / "containers").resolve()
    assert f":{run.home}:/bin/bash" in run.passwd.read_text(encoding="utf-8")
    ee.remove_run(run)
    assert not run.root.exists()


def test_engine_connections_are_found_when_xdg_config_home_points_elsewhere(tmp_path):
    (tmp_path / ".config" / "containers").mkdir(parents=True)
    run = ee.prepare_run("r1", home=tmp_path, env={"XDG_CONFIG_HOME": str(tmp_path / "other")})
    assert (run.engine_home / ".config" / "containers").is_dir()


def test_stale_runs_are_removed_and_fresh_ones_are_kept(tmp_path):
    runs = ee.last_run_dir(tmp_path) / ee.RUNS_DIRNAME
    stale = runs / "stale"
    fresh = runs / "fresh"
    stale.mkdir(parents=True)
    fresh.mkdir()
    os.utime(stale, (1, 1))
    ee.prepare_run("new", home=tmp_path, env={})
    assert not stale.exists()
    assert fresh.exists()


def test_operator_vars_are_a_copy(tmp_path):
    source = tmp_path / "cfg" / "ansible-plaibook" / "vars.yml"
    source.parent.mkdir(parents=True)
    source.write_text("agent_family: openai\n", encoding="utf-8")
    run = ee.prepare_run("r1", home=tmp_path, env={"XDG_CONFIG_HOME": str(tmp_path / "cfg")})
    copied = run.home / ".config" / "ansible-plaibook" / "vars.yml"
    assert copied.read_text(encoding="utf-8") == "agent_family: openai\n"
    assert not copied.is_symlink()
    assert stat.S_IMODE(copied.stat().st_mode) == 0o600


def test_command_uses_a_playbook_path_runner_cannot_mount_read_write(tmp_path):
    run = _run_dirs(tmp_path)
    command = _command(run, tmp_path)
    assert command[:3] == ["ansible-navigator", "run", "/plaibook/playbook/review.yml"]
    assert not Path("/plaibook/playbook").exists()
    assert f"--co=--volume={REPO_ROOT}:/plaibook/playbook:ro" in command
    assert f"--co=--volume={run.passwd}:/etc/passwd:ro" in command
    assert "--eev" not in command
    assert command[command.index("--pae") + 1] == "false"
    assert "--penv" in command and "OPENAI_API_KEY" in command
    assert SECRET not in " ".join(command)
    assert f"ANSIBLE_ASYNC_DIR={run.home}/.ansible_async" in command
    assert "ANSIBLE_CALLBACK_PLUGINS=/plaibook/playbook/callback_plugins" in command
    assert not any(part.startswith("ANSIBLE_COLLECTIONS_PATH") for part in command)


def test_wheel_layout_mounts_the_package_and_imports_from_its_parent(tmp_path):
    package = tmp_path / "site" / "plaibook"
    share = package / "share"
    share.mkdir(parents=True)
    mounts, root, python_path = ee.playbook_mounts(share, package)
    assert mounts == [(package.resolve(), "/plaibook/lib/plaibook")]
    assert root == "/plaibook/lib/plaibook/share"
    assert python_path == "/plaibook/lib"


def test_path_with_a_colon_is_refused(tmp_path):
    run = _run_dirs(tmp_path)
    with pytest.raises(ee.EeError, match="cannot express"):
        _command(run, tmp_path, reviews_root=tmp_path / "a:b")


def _last_run(run: ee.EeRun, document: dict) -> Path:
    path = run.cache / f"last_run.{run.run_id}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _publish(run: ee.EeRun, tmp_path: Path) -> Path | None:
    return ee.publish_results(
        run,
        reviews_root=tmp_path / "reviews",
        ledger_dir=tmp_path / "ledger",
        ledger_name="host",
        home=tmp_path,
    )


def test_publish_copies_the_run_scoped_file_and_only_this_runs_ledger_lines(tmp_path):
    run = _run_dirs(tmp_path)
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    _last_run(run, {"targets": [{"summary_path": str(reviews / "summary.json")}]})
    run.ledger.mkdir()
    lines = [
        json.dumps({"run_id": run.run_id, "status": "ok"}),
        json.dumps({"run_id": "someone-else"}),
        "not json",
    ]
    (run.ledger / "host.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _publish(run, tmp_path) == last_run_path(run.run_id, tmp_path)
    assert not (ee.last_run_dir(tmp_path) / "last_run.json").exists()
    written = (tmp_path / "ledger" / "host.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["run_id"] for line in written] == [run.run_id]


def test_publish_without_a_last_run_file_returns_none(tmp_path):
    assert _publish(_run_dirs(tmp_path), tmp_path) is None


def test_publish_refuses_a_link_out_of_the_run(tmp_path):
    run = _run_dirs(tmp_path)
    outside = tmp_path / "private.json"
    outside.write_text("{}", encoding="utf-8")
    (run.cache / f"last_run.{run.run_id}.json").symlink_to(outside)
    with pytest.raises(ee.EeError, match="leaves the run directory"):
        _publish(run, tmp_path)
    assert not last_run_path(run.run_id, tmp_path).exists()


def test_publish_refuses_a_replaced_cache_directory(tmp_path):
    run = _run_dirs(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / f"last_run.{run.run_id}.json").write_text("{}", encoding="utf-8")
    shutil.rmtree(run.home / ".cache")
    (run.home / ".cache").mkdir()
    (run.home / ".cache" / "ansible-plaibook").symlink_to(outside)
    with pytest.raises(ee.EeError, match="leaves the run directory"):
        _publish(run, tmp_path)


def test_publish_refuses_a_target_that_names_a_host_file(tmp_path):
    run = _run_dirs(tmp_path)
    _last_run(run, {"targets": [{"summary_run_scoped_path": str(tmp_path / ".ssh" / "config.json")}]})
    with pytest.raises(ee.EeError, match="outside"):
        _publish(run, tmp_path)
    assert not last_run_path(run.run_id, tmp_path).exists()


def _ee_args(*extra: str):
    return build_parser("plai").parse_args(
        ["review", "org/repo/1", "--ee", "-e", "agent_family=openai", *extra]
    )


@pytest.fixture
def ee_host(tmp_path, monkeypatch):
    """A host with a navigator and an engine, and no real container or home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr("plaibook.ee.navigator_bin", lambda: "ansible-navigator")
    monkeypatch.setattr("plaibook.ee.find_engine", lambda: "podman")
    monkeypatch.setattr("plaibook.ee.container_options", lambda run, engine, env: ["--user=1:1"])
    monkeypatch.setattr("plaibook.ee.github_token_from_cli", lambda env=None: None)
    monkeypatch.setattr("plaibook.cli.ensure_collections", lambda *a, **k: pytest.fail("host collections installed"))
    monkeypatch.setattr("plaibook.cli.ensure_provider_sdk", lambda *a, **k: pytest.fail("host SDK installed"))
    monkeypatch.setattr("plaibook.cli.reexec_sandbox_runtime", lambda **k: pytest.fail("sandbox runtime re-exec"))
    return home


def test_review_runs_navigator_publishes_and_removes_the_run_tree(ee_host, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    seen = {}

    def fake_group(command, *, env, verbose, timeout, **kwargs):
        seen["command"] = command
        seen["env"] = env
        seen["kwargs"] = kwargs
        extras = json.loads(command[command.index("-e") + 1])
        seen["extras"] = extras
        cache = Path(kwargs["cwd"]) / ".cache" / "ansible-plaibook"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"last_run.{extras['last_run_id']}.json").write_text(
            json.dumps({"targets": [{"target": "org/repo/1", "verdict": "READY_FOR_HUMAN_REVIEW"}]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("plaibook.ee.verify_release_image", lambda image: image)
    monkeypatch.setattr("plaibook.cli.run_process_group", fake_group)
    assert cmd_review(_ee_args()) == 0
    assert "READY_FOR_HUMAN_REVIEW" in capsys.readouterr().out
    extras = seen["extras"]
    assert extras["use_sandbox"] is False
    assert extras["review_runtime_from_image"] is True
    assert extras["review_output_base"] == str((ee_host / "reviews").resolve())
    assert "controller_python" not in extras
    assert seen["env"]["OPENAI_API_KEY"] == SECRET
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert seen["env"]["HOME"] != str(ee_host)
    assert seen["kwargs"]["stdin"] == subprocess.DEVNULL
    assert SECRET not in " ".join(seen["command"])
    assert last_run_path(extras["last_run_id"], ee_host).is_file()
    assert list((ee.last_run_dir(ee_host) / ee.RUNS_DIRNAME).iterdir()) == []


def test_timeout_removes_the_container_and_the_run_tree(ee_host, monkeypatch):
    stopped = []

    def fake_group(command, **kwargs):
        raise PlaybookTimeoutError(1, command)

    monkeypatch.setattr("plaibook.ee.verify_release_image", lambda image: image)
    monkeypatch.setattr("plaibook.cli.run_process_group", fake_group)
    monkeypatch.setattr("plaibook.ee.stop_run_containers", lambda engine, run_id, env: stopped.append(run_id))
    assert cmd_review(_ee_args()) == 2
    assert len(stopped) == 1
    assert list((ee.last_run_dir(ee_host) / ee.RUNS_DIRNAME).iterdir()) == []


def test_unreachable_engine_stops_before_the_review(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setattr("plaibook.ee.navigator_bin", lambda: "ansible-navigator")
    monkeypatch.setattr("plaibook.ee.find_engine", lambda: "podman")
    monkeypatch.setattr("plaibook.ee.github_token_from_cli", lambda env=None: None)

    def broken(command, **kwargs):
        return subprocess.CompletedProcess(command, 125, "", "cannot connect")

    monkeypatch.setattr("plaibook.ee.subprocess.run", broken)
    monkeypatch.setattr("plaibook.cli.run_process_group", lambda *a, **k: pytest.fail("review started"))
    assert cmd_review(_ee_args()) == 2
    assert "cannot reach its container runtime: cannot connect" in capsys.readouterr().err
    assert list((ee.last_run_dir(tmp_path) / ee.RUNS_DIRNAME).iterdir()) == []


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["review", "--commit", "--ee"], "local commit"),
        (["review", "--ee"], "local commit"),
        (["review", "--branch", "/", "--ee"], "local path"),
        (["review", "org/repo/1", "--ee", "--sandbox"], "--ee and --sandbox"),
        (["review", "org/repo/1", "--ee", "-e", "agent_family=claude_cli"], "claude_cli"),
        (["review", "org/repo/1", "--ee-image", "x"], "--ee-image needs --ee"),
    ],
)
def test_unsupported_combinations_are_refused(argv, message, ee_host, monkeypatch, capsys):
    monkeypatch.setattr("plaibook.cli.run_process_group", lambda *a, **k: pytest.fail("review started"))
    assert cmd_review(build_parser("plai").parse_args(argv)) == 2
    assert message in capsys.readouterr().err


def test_stop_run_containers_filters_on_this_run(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "c1\n" if command[1] == "ps" else "", "")

    monkeypatch.setattr("plaibook.ee.subprocess.run", fake_run)
    ee.stop_run_containers("podman", "RUN", {})
    assert calls[0] == ["podman", "ps", "-aq", "--filter", f"label={ee.RUN_LABEL}=RUN"]
    assert calls[1] == ["podman", "rm", "-f", "c1"]


def _tasks(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_image_runtime_skips_host_collection_and_sdk_installs():
    tasks = {task["name"]: task for task in _tasks(REPO_ROOT / "roles/review/tasks/ensure_runtime_deps.yml")}
    gate = "not (review_runtime_from_image | default(false) | bool)"
    assert tasks["Ensure review collections are installed"]["when"] == gate
    assert tasks["Ensure the selected provider Python SDK is installed"]["when"] == gate


def test_ledger_host_lookup_works_without_a_hostname_binary():
    """The execution environment has no hostname command. default() evaluates its argument anyway."""
    for name in ("roles/review/tasks/write_last_run_files.yml", "roles/review/defaults/main.yml"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "'hostname -s'" not in text
        assert "hostname -s 2>/dev/null || uname -n" in text


@pytest.mark.skipif(shutil.which("ansible-navigator") is None, reason="ansible-navigator is not installed")
def test_real_navigator_builds_a_run_without_ssh_or_the_checkout_read_write(tmp_path):
    """Real navigator and ansible-runner, with a recording stand-in for the engine."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "record.json"
    (bindir / "podman").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = run ]; then\n'
        f'  python3 -c \'import json,sys; json.dump(sys.argv[1:], open("{record}", "w"))\' "$@"\n'
        "fi\n"
        'if [ "$1" = image ]; then echo \'[{"Id": "abc"}]\'; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (bindir / "podman").chmod(0o755)
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    agent = home / ".ssh" / "agent"
    agent.write_text("", encoding="utf-8")
    run = ee.prepare_run("realnav", home=home, env={})
    reviews = (home / "reviews").resolve()
    reviews.mkdir()
    env = ee.engine_env(
        run,
        {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "SSH_AUTH_SOCK": str(agent),
            "ANSIBLE_COLLECTIONS_PATH": "/host/collections",
        },
    )
    env["OPENAI_API_KEY"] = SECRET
    command = _command(
        run,
        tmp_path,
        navigator=shutil.which("ansible-navigator"),
        reviews_root=reviews,
        options=["--user=1:1", "--ipc=private"],
    )
    completed = subprocess.run(
        command,
        env=env,
        cwd=run.home,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    argv = json.loads(record.read_text(encoding="utf-8"))
    joined = " ".join(argv)
    assert ".ssh" not in joined
    assert "SSH_AUTH_SOCK" not in joined
    assert SECRET not in joined
    assert "/host/collections" not in joined
    assert "--user=root" not in argv
    assert argv.index("--ipc=host") < argv.index("--ipc=private")
    mounted = [argv[index + 1] for index, part in enumerate(argv) if part == "-v"]
    assert not any(str(REPO_ROOT) in volume for volume in mounted)
    assert f"--volume={REPO_ROOT}:/plaibook/playbook:ro" in argv
    assert f"--volume={run.passwd}:/etc/passwd:ro" in argv
    assert argv[argv.index("ansible-playbook") + 1] == "/plaibook/playbook/review.yml"
