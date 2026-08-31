#!/usr/bin/env python3
"""Post ansible-plaibook's rendered findings.md (see
roles/review/templates/findings.md.j2) as a comment on a GitHub PR,
GitLab MR, or Gitea PR.

Usage:
  post_review_comment.py gitlab <project> <mr_iid> <findings_file> [--host <host>]
  post_review_comment.py github <owner/repo> <pr_number> <findings_file>
  post_review_comment.py gitea <owner/repo> <pr_number> <findings_file> [--host <host>]

Authentication via environment variables:
  GITHUB_TOKEN / GH_TOKEN (GitHub)
  GITLAB_TOKEN / GLAB_TOKEN (GitLab)
  GITEA_TOKEN / TEA_TOKEN (Gitea)
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# Mirrors parse_pr_target.yml's own Ansible-side assert -- this script
# can be invoked standalone outside the pipeline, so the same
# safe-to-embed guarantee needs to hold here independently.
_SAFE_PROJECT_RE = re.compile(r"^[A-Za-z0-9_~][A-Za-z0-9_.~/-]*$")
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*(?::\d+)?$")


def http_post(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: int = 60) -> tuple[int, str]:
    """Perform an HTTP POST request with JSON body."""
    req_headers = {
        "User-Agent": "ansible-plaibook",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    cf_id = os.environ.get("CF_ACCESS_CLIENT_ID") or os.environ.get("CLOUDFLARE_ACCESS_CLIENT_ID")
    cf_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET") or os.environ.get("CLOUDFLARE_ACCESS_CLIENT_SECRET")
    if cf_id and cf_secret:
        req_headers["CF-Access-Client-Id"] = cf_id
        req_headers["CF-Access-Client-Secret"] = cf_secret

    if headers:
        req_headers.update(headers)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8")
        return e.code, err
    except Exception as e:
        return 0, str(e)


import shutil
import subprocess

def get_github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token and shutil.which("gh"):
        try:
            res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                token = res.stdout.strip()
        except Exception:
            pass
    return token


def get_gitlab_token() -> str | None:
    token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GLAB_TOKEN")
    if not token and shutil.which("glab"):
        try:
            res = subprocess.run(["glab", "auth", "token"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                token = res.stdout.strip()
        except Exception:
            pass
    return token


def get_gitea_token() -> str | None:
    return os.environ.get("GITEA_TOKEN") or os.environ.get("TEA_TOKEN")


def post_gitlab(project: str, mr_iid: str, findings_file: str, host: str) -> None:
    token = get_gitlab_token()
    headers = {}
    if token:
        headers["PRIVATE-TOKEN"] = token

    with open(findings_file, "r", encoding="utf-8") as f:
        body = f.read()

    encoded = urllib.parse.quote(project, safe="")
    url = f"https://{host}/api/v4/projects/{encoded}/merge_requests/{mr_iid}/notes"

    status, resp = http_post(url, {"body": body}, headers=headers)
    if status not in (200, 201):
        print(f"Error posting to MR !{mr_iid} on {host} (HTTP {status}): {resp}", file=sys.stderr)
        sys.exit(1)
    print(f"Review posted to MR !{mr_iid}")


def post_github(repo: str, pr_number: str, findings_file: str) -> None:
    token = get_github_token()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with open(findings_file, "r", encoding="utf-8") as f:
        body = f.read()

    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"

    status, resp = http_post(url, {"body": body}, headers=headers)
    if status not in (200, 201):
        print(f"Error posting to PR #{pr_number} on github.com (HTTP {status}): {resp}", file=sys.stderr)
        sys.exit(1)
    print(f"Review posted to PR #{pr_number}")


def post_gitea(project: str, pr_number: str, findings_file: str, host: str) -> None:
    token = os.environ.get("GITEA_TOKEN") or os.environ.get("TEA_TOKEN")
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"

    with open(findings_file, "r", encoding="utf-8") as f:
        body = f.read()

    url = f"https://{host}/api/v1/repos/{project}/issues/{pr_number}/comments"

    status, resp = http_post(url, {"body": body}, headers=headers)
    if status not in (200, 201):
        print(f"Error posting to PR #{pr_number} on {host} (HTTP {status}): {resp}", file=sys.stderr)
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

    if not _SAFE_PROJECT_RE.match(project_or_repo):
        print(f"Error: unsafe characters in project/repo: {project_or_repo}", file=sys.stderr)
        sys.exit(1)

    host = ""
    if "--host" in sys.argv[5:]:
        idx = sys.argv.index("--host", 5)
        if idx + 1 < len(sys.argv):
            host = sys.argv[idx + 1]

    if host and not _SAFE_HOST_RE.match(host):
        print(f"Error: unsafe characters in host: {host}", file=sys.stderr)
        sys.exit(1)

    if platform == "gitlab":
        post_gitlab(project_or_repo, number, findings_file, host or "gitlab.com")
    elif platform == "github":
        post_github(project_or_repo, number, findings_file)
    elif platform == "gitea":
        if not host:
            print("Error: --host is required for gitea platform", file=sys.stderr)
            sys.exit(1)
        post_gitea(project_or_repo, number, findings_file, host)
    else:
        print(f"Unknown platform: {platform}. Use 'github', 'gitlab', or 'gitea'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

