#!/usr/bin/env python3
"""Move the OpenShell gateway, sandbox image, and Python SDK to their latest releases.

Each pin stays an exact version, commit, checksum, or digest, so a run always
installs what a reviewed pull request named. Old values are also replaced in the
docs that quote them. The SDK has to stay on the gateway's 0.1 line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / ".github" / "openshell-pins.env"
DOCS = (ROOT / "docs" / "dependency-map.md", ROOT / "docs" / "github-review-check.md")
REPO = "NVIDIA/OpenShell"
IMAGE_REPO = "nvidia/openshell-community/sandboxes/base"
IMAGE_TAG = "latest"
TIMEOUT_SECONDS = 60
MANIFEST_TYPES = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
EE_IN = ROOT / "execution-environment-requirements.in"
_SDK_PIN = re.compile(r"(?m)^openshell==(\S+)$")
_SDK_LINE = re.compile(r"\d+\.\d+\.\d+")
SDK_SERIES = "0.1."
SDK_COMPILES = (
    (
        "uv pip compile --generate-hashes --python-version 3.11 --no-annotate"
        " plaibook/hashed/openshell.in -o plaibook/hashed/openshell-requirements.txt"
        " --upgrade-package openshell"
    ),
    (
        "uv pip compile --generate-hashes --universal --python-version 3.12 --no-annotate --no-strip-extras"
        " execution-environment-requirements.in -o execution-environment-requirements.txt"
        " --upgrade-package openshell"
    ),
    "uv lock --upgrade-package openshell",
)
_TAG_RE = re.compile(r"v\d+\.\d+\.\d+")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def _open(url: str, headers: dict[str, str] | None = None):
    request = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)  # noqa: S310


def _github_json(path: str) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with _open(f"https://api.github.com/repos/{REPO}/{path}", headers) as response:
        return json.load(response)


def latest_pins() -> dict[str, str]:
    """Read the newest stable release and image. Raises ValueError on odd answers."""
    tag = str(_github_json("releases/latest").get("tag_name", ""))
    if not _TAG_RE.fullmatch(tag):
        raise ValueError(f"unexpected release tag {tag!r}")
    commit = str(_github_json(f"commits/{tag}").get("sha", ""))
    if not _SHA_RE.fullmatch(commit):
        raise ValueError(f"unexpected commit {commit!r} for {tag}")
    with _open(f"https://raw.githubusercontent.com/{REPO}/{commit}/install.sh") as response:
        checksum = hashlib.sha256(response.read()).hexdigest()
    scope = f"repository:{IMAGE_REPO}:pull"
    with _open(f"https://ghcr.io/token?scope={scope}") as response:
        token = json.load(response)["token"]
    headers = {"Authorization": f"Bearer {token}", "Accept": MANIFEST_TYPES}
    with _open(f"https://ghcr.io/v2/{IMAGE_REPO}/manifests/{IMAGE_TAG}", headers) as response:
        digest = response.headers.get("Docker-Content-Digest", "")
    if not _DIGEST_RE.fullmatch(digest):
        raise ValueError(f"unexpected image digest {digest!r}")
    return {
        "OPENSHELL_VERSION": tag,
        "OPENSHELL_INSTALLER_COMMIT": commit,
        "OPENSHELL_INSTALLER_SHA256": checksum,
        "SANDBOX_IMAGE": f"ghcr.io/{IMAGE_REPO}@{digest}",
    }


def latest_sdk() -> str:
    with _open("https://pypi.org/pypi/openshell/json") as response:
        version = str(json.load(response)["info"]["version"])
    if not _SDK_LINE.fullmatch(version) or not version.startswith(SDK_SERIES):
        raise ValueError(f"openshell {version} is not a stable release on the {SDK_SERIES}x line")
    return version


def move_sdk(new: str) -> str | None:
    """Rewrite the SDK pin and recompile its locks. Returns the old version if it moved."""
    text = EE_IN.read_text(encoding="utf-8")
    match = _SDK_PIN.search(text)
    if not match:
        raise ValueError(f"no openshell pin in {EE_IN.name}")
    old = match.group(1)
    if old == new:
        return None
    EE_IN.write_text(_SDK_PIN.sub(f"openshell=={new}", text), encoding="utf-8")
    for command in SDK_COMPILES:
        subprocess.run(command.split(), check=True, cwd=ROOT, timeout=300)  # noqa: S603
    docs = ROOT / "docs" / "dependency-map.md"
    moved = docs.read_text(encoding="utf-8").replace(f"openshell=={old}", f"openshell=={new}")
    docs.write_text(moved, encoding="utf-8")
    return old


def read_pins(text: str) -> dict[str, str]:
    pins = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            name, _, value = line.partition("=")
            pins[name] = value
    return pins


def apply_pins(text: str, new: dict[str, str]) -> str:
    """Rewrite values in place, keeping comments and order."""
    lines = []
    for line in text.splitlines():
        name = line.partition("=")[0]
        lines.append(f"{name}={new[name]}" if not line.startswith("#") and name in new else line)
    return "\n".join(lines) + "\n"


def changed_values(old: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    return {old[name]: new[name] for name in new if name in old and old[name] != new[name]}


def replace_values(text: str, replacements: dict[str, str]) -> str:
    for before, after in replacements.items():
        text = text.replace(before, after)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite the pin file and docs")
    parser.add_argument("--pr-body", metavar="PATH", help="write a markdown pull request body to PATH")
    args = parser.parse_args(argv)

    text = PINS.read_text(encoding="utf-8")
    old = read_pins(text)
    try:
        new = latest_pins()
    except (OSError, ValueError, KeyError) as exc:
        print(f"could not read the latest OpenShell pins: {exc}", file=sys.stderr)
        return 1
    replacements = changed_values(old, new)
    sdk_from = sdk_to = None
    if args.write:
        try:
            sdk_to = latest_sdk()
            sdk_from = move_sdk(sdk_to)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            print(f"could not move the OpenShell SDK: {exc}", file=sys.stderr)
            return 1
    if not replacements and not sdk_from:
        print("OpenShell pins are current.")
        return 0
    for name, value in new.items():
        if old.get(name) != value:
            print(f"{name}: {old.get(name)} -> {value}")
    if args.write:
        PINS.write_text(apply_pins(text, new), encoding="utf-8")
        for path in DOCS:
            path.write_text(replace_values(path.read_text(encoding="utf-8"), replacements), encoding="utf-8")
    if args.pr_body:
        moved = [name for name in new if old.get(name) != new[name]]
        rows = "\n".join(f"- `{name}`: `{old.get(name)}` to `{new[name]}`" for name in moved)
        sdk_row = f"\n- `openshell` SDK: `{sdk_from}` to `{sdk_to}`" if sdk_from else ""
        Path(args.pr_body).write_text(
            f"Moves OpenShell to the latest release.\n\n{rows}{sdk_row}\n\n"
            "The OpenShell smoke check on this pull request starts the new gateway and a sandbox. "
            "Reviews keep their pinned workflow until `scripts/move_review_pin.py` moves "
            "`review.checkout` and then `review.caller`.\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
