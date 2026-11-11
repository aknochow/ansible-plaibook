#!/usr/bin/env python3
"""Check that dist/ holds one sdist and one wheel that carry the playbook.

release-cut.yml runs this before it tags, and publish.yml runs it before
the upload, so a build that cannot vendor the playbook never gets a tag.
`uv build` builds the wheel from the sdist, so a file missing from the
sdist fails the build itself.
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path

WHEEL_FILES = (
    "plaibook/share/review.yml",
    "plaibook/share/ansible.cfg",
    "plaibook/share/collections-requirements.yml",
    "plaibook/share/skills/ansible-plaibook-review/SKILL.md",
)


def check_dist(dist: Path) -> str:
    wheels = sorted(dist.glob("plaibook-*.whl"))
    sdists = sorted(dist.glob("plaibook-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        found = sorted(path.name for path in dist.iterdir()) if dist.is_dir() else []
        raise ValueError(f"expected one wheel and one sdist in {dist}: {found}")
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    missing = [name for name in WHEEL_FILES if name not in names]
    if missing:
        raise ValueError(f"{wheels[0].name} is missing {', '.join(missing)}")
    with tarfile.open(sdists[0], "r:gz") as archive:
        members = archive.getnames()
    if not any(name.endswith("/review.yml") for name in members):
        raise ValueError(f"{sdists[0].name} is missing review.yml")
    return wheels[0].name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dist", type=Path, nargs="?", default=Path("dist"))
    args = parser.parse_args(argv)
    try:
        wheel = check_dist(args.dist)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"vendored playbook ok: {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
