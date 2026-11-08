# -*- coding: utf-8 -*-
"""Copy the playbook tree into plaibook/share so wheels do not need a checkout."""

from __future__ import annotations

import shutil
from pathlib import Path

SHARE_DIRNAME = "share"
SHARE_FILES = (
    "review.yml",
    "ansible.cfg",
    "collections-requirements.yml",
)
SHARE_TREES = (
    "roles",
    "action_plugins",
    "library",
    "filter_plugins",
    "callback_plugins",
)
SKILL_RELATIVE = Path(".agents/skills/ansible-plaibook-review/SKILL.md")
_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    "*.retry",
    ".git",
    "test_*.py",
    "tests",
)


def share_destination(package_dir: Path | None = None) -> Path:
    root = package_dir if package_dir is not None else Path(__file__).resolve().parent
    return root / SHARE_DIRNAME


def materialize_playbook_share(repo_root: Path, dest: Path | None = None) -> Path:
    """Copy review.yml and the Ansible plugin/role trees into *dest*.

    Called from setuptools ``build_py`` so ``pip install plaibook`` ships a
    runnable playbook, not just the CLI wrapper.
    """
    source_root = Path(repo_root).resolve()
    target = Path(dest).resolve() if dest is not None else share_destination()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    missing: list[str] = []
    for name in SHARE_FILES:
        src = source_root / name
        if not src.is_file():
            missing.append(name)
            continue
        shutil.copy2(src, target / name)
    for name in SHARE_TREES:
        src = source_root / name
        if not src.is_dir():
            missing.append(name)
            continue
        shutil.copytree(src, target / name, ignore=_IGNORE)

    skill_src = source_root / SKILL_RELATIVE
    if not skill_src.is_file():
        missing.append(str(SKILL_RELATIVE))
    else:
        skill_dest = target / "skills" / "ansible-plaibook-review" / "SKILL.md"
        skill_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_src, skill_dest)

    if missing:
        raise FileNotFoundError(
            "Cannot vendor the plaibook playbook into the wheel; missing "
            + ", ".join(missing)
            + f" under {source_root}"
        )
    if not (target / "review.yml").is_file() or not (target / "ansible.cfg").is_file():
        raise FileNotFoundError(f"Vendored playbook at {target} is incomplete")
    return target


try:
    from setuptools.command.build_py import build_py as _build_py
except ImportError:  # pragma: no cover - setuptools is the build backend
    _build_py = object  # type: ignore[misc, assignment]


class build_py(_build_py):  # type: ignore[valid-type, misc]
    def run(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        materialize_playbook_share(repo_root, share_destination())
        super().run()
