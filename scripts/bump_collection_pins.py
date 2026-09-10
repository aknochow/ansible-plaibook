#!/usr/bin/env python3
"""Refresh SHA pins in collections-requirements.yml to each repo's default-branch HEAD.

Dependabot has no Ansible Galaxy / collections-requirements.yml ecosystem, so
`.github/dependabot.yml` cannot keep git SHA pins current. Floating
`version: main` entries are left alone — those already track HEAD. Only
`type: git` collections whose `version` is a hex SHA are bumped.

Usage:
    python3 scripts/bump_collection_pins.py              # dry-run
    python3 scripts/bump_collection_pins.py --write
    python3 scripts/bump_collection_pins.py --check      # exit 1 if stale
    python3 scripts/bump_collection_pins.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = REPO_ROOT / "collections-requirements.yml"
GITHUB_API = "https://api.github.com"
USER_AGENT = "ansible-plaibook-bump-collection-pins"

NAME_RE = re.compile(r"^(\s*)- name:\s*(\S+)\s*$")
TYPE_RE = re.compile(r"^\s+type:\s*(\S+)\s*$")
VERSION_RE = re.compile(
    r"^(\s+version:\s*)(?P<q>['\"]?)(?P<val>[^'\"#\s]+)(?P=q)\s*(?:#.*)?$"
)
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
GITHUB_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class GitShaPin:
    name: str
    owner: str
    repo: str
    current: str
    version_line: int
    version_prefix: str
    version_quote: str


@dataclass(frozen=True)
class PinChange:
    name: str
    owner: str
    repo: str
    current: str
    latest: str

    @property
    def stale(self) -> bool:
        return not _sha_matches(self.current, self.latest)


def _sha_matches(current: str, latest: str) -> bool:
    """True when the pinned value already names latest (full or abbreviated)."""
    current = current.lower()
    latest = latest.lower()
    return current == latest or latest.startswith(current) or current.startswith(latest)


def parse_git_sha_pins(text: str) -> list[GitShaPin]:
    """Return SHA-pinned `type: git` collections, preserving source line indexes."""
    lines = text.splitlines()
    pins: list[GitShaPin] = []
    current: dict | None = None

    def _flush() -> None:
        nonlocal current
        if current is None:
            return
        version = current.get("version")
        if current.get("type") == "git" and isinstance(version, str) and SHA_RE.match(version):
            url_match = GITHUB_URL_RE.match(current["name"])
            if url_match:
                pins.append(
                    GitShaPin(
                        name=current["name"],
                        owner=url_match.group("owner"),
                        repo=url_match.group("repo"),
                        current=version,
                        version_line=current["version_line"],
                        version_prefix=current["version_prefix"],
                        version_quote=current["version_quote"],
                    )
                )
        current = None

    for index, line in enumerate(lines):
        name_match = NAME_RE.match(line)
        if name_match:
            _flush()
            current = {"name": name_match.group(2)}
            continue
        if current is None:
            continue
        type_match = TYPE_RE.match(line)
        if type_match:
            current["type"] = type_match.group(1)
            continue
        version_match = VERSION_RE.match(line)
        if version_match:
            current["version"] = version_match.group("val")
            current["version_line"] = index
            current["version_prefix"] = version_match.group(1)
            current["version_quote"] = version_match.group("q")
    _flush()
    return pins


def apply_pin_changes(text: str, changes: list[PinChange], pins: list[GitShaPin]) -> str:
    """Replace stale SHA version lines; keep comments and every other line intact."""
    latest_by_name = {change.name: change.latest for change in changes if change.stale}
    if not latest_by_name:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    raw_lines = text.splitlines()
    ended_with_newline = text.endswith("\n")
    for pin in pins:
        latest = latest_by_name.get(pin.name)
        if latest is None:
            continue
        quote = pin.version_quote
        raw_lines[pin.version_line] = f"{pin.version_prefix}{quote}{latest}{quote}"
    new_text = newline.join(raw_lines)
    if ended_with_newline:
        new_text += newline
    return new_text


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_json(url: str, *, opener=None) -> dict:
    request = urllib.request.Request(url, headers=_github_headers())
    reader = opener or urllib.request.urlopen
    try:
        with reader(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GET {url} failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc.reason}") from exc


def fetch_default_branch_sha(owner: str, repo: str, *, opener=None) -> str:
    repo_url = f"{GITHUB_API}/repos/{owner}/{repo}"
    repo_info = fetch_json(repo_url, opener=opener)
    default_branch = repo_info.get("default_branch") or "main"
    commit_url = f"{GITHUB_API}/repos/{owner}/{repo}/commits/{default_branch}"
    commit = fetch_json(commit_url, opener=opener)
    sha = commit.get("sha")
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        raise RuntimeError(f"no commit SHA at {commit_url}")
    return sha


def plan_changes(
    pins: list[GitShaPin],
    *,
    sha_fetcher=None,
) -> list[PinChange]:
    fetcher = sha_fetcher or fetch_default_branch_sha
    changes: list[PinChange] = []
    for pin in pins:
        latest = fetcher(pin.owner, pin.repo)
        changes.append(
            PinChange(
                name=pin.name,
                owner=pin.owner,
                repo=pin.repo,
                current=pin.current,
                latest=latest,
            )
        )
    return changes


def format_pr_body(changes: list[PinChange]) -> str:
    stale = [change for change in changes if change.stale]
    lines = [
        "## Summary",
        "",
        "Bump SHA-pinned git collections in `collections-requirements.yml` to each",
        "repo's default-branch HEAD. Dependabot cannot update this file.",
        "",
        "## Changes",
        "",
    ]
    if not stale:
        lines.append("No stale SHA pins.")
        lines.append("")
        return "\n".join(lines)
    for change in stale:
        compare = (
            f"https://github.com/{change.owner}/{change.repo}/compare/"
            f"{change.current}...{change.latest}"
        )
        lines.append(
            f"- `{change.owner}/{change.repo}`: `{change.current[:12]}` → "
            f"`{change.latest[:12]}` ([compare]({compare}))"
        )
    lines.append("")
    return "\n".join(lines)


def _print_plan(changes: list[PinChange]) -> None:
    if not changes:
        print("No SHA-pinned git collections found.")
        return
    for change in changes:
        status = "stale" if change.stale else "current"
        print(f"{change.owner}/{change.repo}: {change.current} -> {change.latest} ({status})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        default=str(DEFAULT_FILE),
        help="collections-requirements.yml path (default: repo root)",
    )
    parser.add_argument("--write", action="store_true", help="rewrite stale SHA pins in place")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any SHA pin is behind default-branch HEAD",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="print machine-readable plan")
    parser.add_argument(
        "--pr-body",
        metavar="PATH",
        help="write a markdown PR body for stale pins to PATH",
    )
    args = parser.parse_args(argv)

    path = Path(args.file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"failed to read {path}: {exc}", file=sys.stderr)
        return 1

    pins = parse_git_sha_pins(text)
    try:
        changes = plan_changes(pins)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    stale = [change for change in changes if change.stale]
    if args.as_json:
        print(
            json.dumps(
                {
                    "file": str(path),
                    "changes": [asdict(change) | {"stale": change.stale} for change in changes],
                    "stale_count": len(stale),
                },
                indent=2,
            )
        )
    else:
        _print_plan(changes)

    if args.pr_body:
        Path(args.pr_body).write_text(format_pr_body(changes), encoding="utf-8")

    if args.write and stale:
        path.write_text(apply_pin_changes(text, changes, pins), encoding="utf-8")
        if not args.as_json:
            print(f"wrote {len(stale)} pin(s) to {path}")

    if args.check and stale:
        print(f"{len(stale)} SHA pin(s) behind default-branch HEAD", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
