# -*- coding: utf-8 -*-
"""Smoke-test a built plaibook execution environment.

Host side (``python scripts/ee_smoke.py --image plaibook-ee:smoke``) runs
the checks inside the image, then compares installed collection versions
with galaxy.yml at each collections-requirements.yml pin.

``--inside`` is the in-image half. It uses only the standard library.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

SOURCE_LABEL = "https://github.com/aknochow/ansible-plaibook"
IMPORTS = (
    "anthropic",
    "claude_agent_sdk",
    "openai",
    "google.genai",
    "cursor_sdk",
    "openshell",
    "kubernetes",
)
_NAME = re.compile(r"^\s*-\s*name:\s*(\S+)\s*$")
_VERSION = re.compile(r"^\s*version:\s*([0-9a-f]{7,40})\b")
_FIELD = re.compile(r"(?m)^([A-Za-z0-9_]+):\s*(\S+)\s*$")
_GITHUB = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$")
COMMAND_TIMEOUT = 120
PODMAN_TIMEOUT = 300


def _run(argv: list[str], timeout: int = COMMAND_TIMEOUT) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(argv), file=sys.stderr)
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _fail(f"{' '.join(argv)} timed out after {timeout}s")
    if completed.stdout:
        print(completed.stdout, file=sys.stderr, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="" if completed.stderr.endswith("\n") else "\n")
    return completed


def _fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _fields(text: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in _FIELD.finditer(text)}


def installed_collections() -> dict[str, str]:
    root = Path("/usr/share/ansible/collections/ansible_collections")
    if not root.is_dir():
        _fail(f"collection root is missing: {root}")
    found: dict[str, str] = {}
    manifests = sorted(root.glob("*/*/MANIFEST.json"))
    if not manifests:
        _fail(f"no collections installed under {root}")
    for manifest in manifests:
        info = json.loads(manifest.read_text(encoding="utf-8")).get("collection_info") or {}
        namespace = info.get("namespace")
        name = info.get("name")
        version = info.get("version")
        if not namespace or not name or not version:
            _fail(f"{manifest} is missing collection_info namespace, name, or version")
        found[f"{namespace}.{name}"] = str(version)
    return found


def smoke_inside() -> None:
    uid = os.getuid()
    if uid != 1000:
        _fail(f"image uid is {uid}, expected 1000")
    if sys.version_info[:2] != (3, 12):
        _fail(f"python is {sys.version}, expected 3.12")
    if Path("/opt/openshell/ssh_proxy.py").exists():
        _fail("ssh_proxy.py is in the image")

    for argv in (
        ["ansible-playbook", "--version"],
        ["gh", "--version"],
        ["glab", "--version"],
    ):
        completed = _run(argv)
        if completed.returncode != 0:
            _fail(f"{argv[0]} exited {completed.returncode}")

    for module in IMPORTS:
        importlib.import_module(module)
        print(f"imported {module}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        playbook = Path(tmp) / "smoke.yml"
        playbook.write_text(
            "---\n"
            "- name: plaibook-ee smoke\n"
            "  hosts: localhost\n"
            "  gather_facts: false\n"
            "  tasks:\n"
            "    - name: prove a local task runs\n"
            "      ansible.builtin.debug:\n"
            "        msg: plaibook-ee-smoke\n",
            encoding="utf-8",
        )
        completed = _run(["ansible-playbook", str(playbook), "-i", "localhost,", "-c", "local"])
        if completed.returncode != 0 or "plaibook-ee-smoke" not in completed.stdout:
            _fail("one-task local playbook did not succeed")

    print(json.dumps({"collections": installed_collections()}, sort_keys=True))


def git_pins(text: str) -> list[tuple[str, str]]:
    pins: list[tuple[str, str]] = []
    name: str | None = None
    for line in text.splitlines():
        name_match = _NAME.match(line)
        if name_match:
            name = name_match.group(1)
            continue
        version_match = _VERSION.match(line)
        if version_match and name:
            pins.append((name, version_match.group(1)))
            name = None
    return pins


def expected_collections(requirements: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for url, sha in git_pins(requirements.read_text(encoding="utf-8")):
        match = _GITHUB.match(url)
        if not match:
            _fail(f"collection pin is not a GitHub URL: {url}")
        org, repo = match.group(1), match.group(2)
        url = f"https://raw.githubusercontent.com/{org}/{repo}/{sha}/galaxy.yml"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                text = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            _fail(f"could not read {org}/{repo} galaxy.yml at {sha}: {exc}")
        fields = _fields(text)
        fqcn = f"{fields['namespace']}.{fields['name']}"
        expected[fqcn] = fields["version"]
    if not expected:
        _fail(f"no git pins in {requirements}")
    return expected


def image_labels(image: str) -> dict[str, str]:
    completed = _run(["podman", "image", "inspect", image], timeout=COMMAND_TIMEOUT)
    if completed.returncode != 0:
        _fail(completed.stderr.strip() or f"podman image inspect {image} failed")
    meta = json.loads(completed.stdout)[0]
    labels = meta.get("Labels") or {}
    config = meta.get("Config") or {}
    user = str(config.get("User") or "")
    if user != "1000":
        _fail(f"image user is {user!r}, expected '1000'")
    return {str(key): str(value) for key, value in labels.items()}


def smoke_image(image: str, requirements: Path) -> None:
    labels = image_labels(image)
    source = labels.get("org.opencontainers.image.source")
    if source != SOURCE_LABEL:
        _fail(f"org.opencontainers.image.source is {source!r}")

    script = Path(__file__).resolve()
    completed = _run(
        [
            "podman",
            "run",
            "--rm",
            "-v",
            f"{script}:/ee_smoke.py:ro",
            image,
            "/usr/bin/python3",
            "/ee_smoke.py",
            "--inside",
        ],
        timeout=PODMAN_TIMEOUT,
    )
    if completed.returncode != 0:
        _fail(f"in-image smoke failed ({completed.returncode})")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        _fail(f"in-image smoke did not print JSON: {exc}")
    installed = payload.get("collections") or {}
    expected = expected_collections(requirements)
    mismatches: list[str] = []
    for fqcn, version in sorted(expected.items()):
        actual = installed.get(fqcn)
        if actual != version:
            mismatches.append(f"{fqcn}: image has {actual!r}, pin has {version}")
    if mismatches:
        _fail("collection versions:\n" + "\n".join(mismatches))
    print(f"smoke ok {image}: {len(expected)} collections at pinned versions")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="Local image name to smoke-test")
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("collections-requirements.yml"),
        help="Pin file whose galaxy versions the image must match",
    )
    parser.add_argument("--inside", action="store_true", help="Run the checks inside the image")
    args = parser.parse_args()
    if args.inside:
        smoke_inside()
        return
    if not args.image:
        _fail("--image is required")
    smoke_image(args.image, args.requirements)


if __name__ == "__main__":
    main()
