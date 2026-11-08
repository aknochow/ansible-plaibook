#!/usr/bin/env python3
"""Choose the next plaibook version and write it into the release files.

Does not tag and does not open a pull request. release-bump.yml does that
after tests/test_version_alignment.yml passes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_PYPROJECT = re.compile(r'(?m)^version\s*=\s*"([^"]+)"')
_MODULE = re.compile(r'(?m)^__version__\s*=\s*"([^"]+)"')
_SKILL = re.compile(r'(?m)^version:\s*"([^"]+)"')
_README_PROSE = re.compile(
    r"`:([0-9]+\.[0-9]+\.[0-9]+)` is the image for tag `v([0-9]+\.[0-9]+\.[0-9]+)`"
)
_README_IMAGE = re.compile(r"ghcr\.io/aknochow/plaibook-ee:([0-9]+\.[0-9]+\.[0-9]+)")


def parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value.strip())
    if not match:
        raise ValueError(f"version must be X.Y.Z, not {value.strip()!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def format_version(parts: tuple[int, int, int]) -> str:
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def next_version(current: str, spec: str) -> str:
    """Return the version a bump of ``current`` by ``spec`` produces.

    ``spec`` is ``patch``, ``minor``, ``major``, or an explicit ``X.Y.Z``.
    An explicit version must be newer than ``current``.
    """
    current_parts = parse_version(current)
    requested = spec.strip()
    if requested == "patch":
        return format_version((current_parts[0], current_parts[1], current_parts[2] + 1))
    if requested == "minor":
        return format_version((current_parts[0], current_parts[1] + 1, 0))
    if requested == "major":
        return format_version((current_parts[0] + 1, 0, 0))
    explicit = format_version(parse_version(requested))
    if parse_version(explicit) <= current_parts:
        raise ValueError(f"explicit version {explicit} must be newer than {current}")
    return explicit


def _one(pattern: re.Pattern[str], text: str, label: str) -> str:
    found = pattern.findall(text)
    if len(found) != 1:
        raise ValueError(f"{label} must contain exactly one version, found {len(found)}")
    if isinstance(found[0], tuple):
        versions = {part for part in found[0] if part}
        if len(versions) != 1:
            raise ValueError(f"{label} version fields disagree: {found[0]}")
        return next(iter(versions))
    return found[0]


def read_version(root: Path) -> str:
    package = _one(_PYPROJECT, (root / "pyproject.toml").read_text(encoding="utf-8"), "pyproject.toml")
    module = _one(_MODULE, (root / "plaibook" / "__init__.py").read_text(encoding="utf-8"), "plaibook/__init__.py")
    skill = _one(
        _SKILL,
        (root / ".agents" / "skills" / "ansible-plaibook-review" / "SKILL.md").read_text(encoding="utf-8"),
        "SKILL.md",
    )
    readme = (root / "README.md").read_text(encoding="utf-8")
    prose = _one(_README_PROSE, readme, "README image sentence")
    image = _one(_README_IMAGE, readme, "README image example")
    versions = {package, module, skill, prose, image}
    if len(versions) != 1:
        raise ValueError(
            "version files disagree: "
            f"pyproject={package} module={module} skill={skill} readme={prose} image={image}"
        )
    return package


def _replace_version(pattern: re.Pattern[str], text: str, new: str, label: str) -> str:
    old = _one(pattern, text, label)

    def repl(match: re.Match[str]) -> str:
        return match.group(0).replace(old, new)

    updated, count = pattern.subn(repl, text, count=1)
    if count != 1 or _one(pattern, updated, label) != new:
        raise ValueError(f"failed to set {label} to {new}")
    return updated


def write_version(root: Path, new: str) -> None:
    parse_version(new)
    targets = {
        root / "pyproject.toml": _PYPROJECT,
        root / "plaibook" / "__init__.py": _MODULE,
        root / ".agents" / "skills" / "ansible-plaibook-review" / "SKILL.md": _SKILL,
        root / "README.md": _README_PROSE,
    }
    updated: dict[Path, str] = {}
    for path, pattern in targets.items():
        updated[path] = _replace_version(pattern, path.read_text(encoding="utf-8"), new, path.name)
    readme = updated[root / "README.md"]
    updated[root / "README.md"] = _replace_version(_README_IMAGE, readme, new, "README image example")
    for path, text in updated.items():
        path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set the plaibook release version in the four release files")
    parser.add_argument("--bump", help="patch, minor, major, or an explicit X.Y.Z")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--print-current", action="store_true", help="Print the current version and do not write")
    args = parser.parse_args(argv)
    try:
        current = read_version(args.root)
        if args.print_current:
            print(current)
            return 0
        if not args.bump:
            parser.error("--bump is required unless --print-current")
        new = next_version(current, args.bump)
        write_version(args.root, new)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
