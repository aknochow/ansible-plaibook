#!/usr/bin/env python3
"""Move .github/openshell-pins.env to the latest OpenShell release and image.

Each pin stays an exact version, commit, checksum, or digest, so a run always
installs what a reviewed pull request named. Old values are also replaced in the
docs that quote them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
    if not replacements:
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
        Path(args.pr_body).write_text(
            f"Moves the OpenShell gateway and sandbox image to the latest release.\n\n{rows}\n\n"
            "The OpenShell smoke check on this pull request starts the new gateway and a sandbox. "
            "The review check keeps using its pinned workflow until `review.checkout` and "
            "`review.caller` move.\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
