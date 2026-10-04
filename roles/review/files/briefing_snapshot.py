#!/usr/bin/env python3
"""One process for the briefing's git diffs, commit count, and file reads.

A sandboxed review pays about 30s per delegated SSH session. Fetching the
diff four ways, then reading markers, then counting commits used to be six
sessions before the checklist. This script does that work in one process.
Checklist commands stay a separate delegated module call: they execute
untrusted project code and must remain inside the sandbox.

argv: repo target_ref source_ref
stdin: {"markers": [relative, ...]}
stdout: {
  "diff": str,
  "name_only": [str, ...],
  "numstat": str,
  "deleted": [str, ...],
  "commit_count": {"rc": int, "stdout": str},
  "markers": {name: bool},
  "go_mod": str | null,
  "files": {path: text}
}

Path rules for markers and file text match read_checkout_files.py.
skills/ and evals/ paths stay in name_only and are omitted from files,
same as briefing.yml's old content-detection filter.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys

_MAX_BYTES = 1048576
_SKIP_CONTENT = re.compile(r"(^|/)(skills|evals)/")


def _ref(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.startswith("-")
        or "\n" in value
        or "\x00" in value
    ):
        sys.stderr.write("ref must be a non-empty revision that does not start with '-'\n")
        sys.exit(2)
    return value


def _lines(text: str) -> list[str]:
    if text.endswith("\n"):
        text = text[:-1]
    if text == "":
        return []
    return text.split("\n")


def _git(repo: str, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        env=env,
    )


def _text(proc: subprocess.CompletedProcess[bytes]) -> str:
    return proc.stdout.decode("utf-8", errors="replace")


def _require(proc: subprocess.CompletedProcess[bytes]) -> str:
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", errors="replace"))
        sys.exit(proc.returncode or 1)
    return _text(proc)


def _resolve(root: str, rel: object):
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\x00" in rel:
        return None
    if ".." in rel.split("/"):
        return None
    path = os.path.join(root, rel)
    try:
        st = os.stat(path, follow_symlinks=True)
    except OSError:
        return None
    real = os.path.realpath(path)
    if real != root and not real.startswith(root + os.sep):
        return None
    return path, st


def _file_text(path: str, size: int) -> str:
    with open(path, "rb") as handle:
        data = handle.read(min(size, _MAX_BYTES))
    return data.decode("utf-8", errors="replace")


def _read_tree(root: str, markers_in: list, files_in: list[str]) -> dict:
    markers = {}
    for name in markers_in:
        resolved = _resolve(root, name)
        markers[name] = resolved is not None
    go_mod = None
    resolved = _resolve(root, "go.mod")
    if resolved is not None and stat.S_ISREG(resolved[1].st_mode):
        go_mod = _file_text(resolved[0], resolved[1].st_size)
    files = {}
    for rel in files_in:
        resolved = _resolve(root, rel)
        if resolved is None:
            continue
        path, st = resolved
        if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_BYTES:
            continue
        files[rel] = _file_text(path, st.st_size)
    return {"markers": markers, "go_mod": go_mod, "files": files}


def main() -> None:
    if len(sys.argv) != 4:
        sys.stderr.write("usage: briefing_snapshot.py <repo> <target_ref> <source_ref>\n")
        sys.exit(2)
    repo = os.path.realpath(sys.argv[1])
    target = _ref(sys.argv[2])
    source = _ref(sys.argv[3])
    request = json.load(sys.stdin)
    diff_args = [target, source, "--"]
    diff = _require(_git(repo, ["diff", *diff_args]))
    name_only = _lines(_require(_git(repo, ["diff", "--name-only", *diff_args])))
    numstat = _require(_git(repo, ["diff", "--numstat", *diff_args]))
    deleted = _lines(_require(_git(repo, ["diff", "--diff-filter=D", "--name-only", *diff_args])))
    count = _git(repo, ["rev-list", "--count", "HEAD"])
    content_files = [path for path in name_only if not _SKIP_CONTENT.search(path)]
    tree = _read_tree(repo, request.get("markers") or [], content_files)
    payload = {
        "diff": diff,
        "name_only": name_only,
        "numstat": numstat,
        "deleted": deleted,
        "commit_count": {
            "rc": count.returncode,
            "stdout": _text(count) if count.returncode == 0 else "",
        },
        "markers": tree["markers"],
        "go_mod": tree["go_mod"],
        "files": tree["files"],
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
