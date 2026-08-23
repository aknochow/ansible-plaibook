#!/usr/bin/env python3
"""Post ansible-plaibook's rendered findings.md (see
roles/review/templates/findings.md.j2) as a comment on a GitHub PR or
GitLab MR.

Usage:
  post_review_comment.py gitlab <project> <mr_iid> <findings_file> --host <host>
  post_review_comment.py github <owner/repo> <pr_number> <findings_file>

Requires: gh (GitHub) or glab (GitLab) CLI, already authenticated.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

# Mirrors parse_pr_target.yml's own Ansible-side assert -- this script
# can be invoked standalone outside the pipeline, so the same
# safe-to-embed-in-a-glab/gh-argv guarantee needs to hold here
# independently, not just at that caller.
_SAFE_PROJECT_RE = re.compile(r"^[A-Za-z0-9_~][A-Za-z0-9_.~/-]*$")
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def post_gitlab(project: str, mr_iid: str, findings_file: str, host: str) -> None:
    with open(findings_file) as f:
        body = f.read()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump({"body": body}, tf)
        payload_file = tf.name
    try:
        encoded = project.replace("/", "%2F")
        result = subprocess.run(
            ["glab", "api", f"projects/{encoded}/merge_requests/{mr_iid}/notes",
             "--hostname", host, "--method", "POST", "--input", payload_file],
            capture_output=True, text=True, timeout=60,
        )
    finally:
        os.unlink(payload_file)
    if result.returncode != 0:
        print(f"Error posting to MR: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    print(f"Review posted to MR !{mr_iid}")


def post_github(repo: str, pr_number: str, findings_file: str) -> None:
    result = subprocess.run(
        ["gh", "pr", "comment", pr_number, "--repo", repo, "--body-file", findings_file],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"Error posting to PR: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    print(f"Review posted to PR #{pr_number}")


def main() -> None:
    if len(sys.argv) < 5:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    platform, project_or_repo, number, findings_file = sys.argv[1:5]

    if not os.path.isfile(findings_file):
        print(f"Error: findings file not found: {findings_file}", file=sys.stderr)
        sys.exit(1)
    try:
        int(number)
    except ValueError:
        print(f"Error: MR/PR number must be an integer, got: {number}", file=sys.stderr)
        sys.exit(1)

    if platform == "gitlab":
        host = "gitlab.com"
        if "--host" in sys.argv[5:]:
            idx = sys.argv.index("--host", 5)
            if idx + 1 < len(sys.argv):
                host = sys.argv[idx + 1]
        if not _SAFE_PROJECT_RE.match(project_or_repo):
            print(f"Error: unsafe characters in project/repo: {project_or_repo}", file=sys.stderr)
            sys.exit(1)
        if not _SAFE_HOST_RE.match(host):
            print(f"Error: unsafe characters in host: {host}", file=sys.stderr)
            sys.exit(1)
        post_gitlab(project_or_repo, number, findings_file, host)
    elif platform == "github":
        if not _SAFE_PROJECT_RE.match(project_or_repo):
            print(f"Error: unsafe characters in project/repo: {project_or_repo}", file=sys.stderr)
            sys.exit(1)
        post_github(project_or_repo, number, findings_file)
    else:
        print(f"Unknown platform: {platform}. Use 'gitlab' or 'github'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
