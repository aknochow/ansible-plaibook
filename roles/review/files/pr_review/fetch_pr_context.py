#!/usr/bin/env python3
"""Fetch PR/MR metadata, CI/pipeline status, and unresolved review
discussions for a single GitHub PR, GitLab MR, or Gitea PR, printed as one
JSON object on stdout — pure data-fetching, no presentation. The caller
(roles/review/tasks/resolve_target_pr.yml) renders that JSON through
roles/review/templates/pr_context.j2 to build review_context, the same
"Python fetches, Jinja presents" split resolve_target_commit.yml
already uses for commit_context.j2.

The caller already knows the platform and hostname from its own
target-parsing regexes and always hands this script a single,
unambiguous, fully-qualified URL -- github.com/OWNER/REPO/pull/N,
HOST/ORG/REPO/-/merge_requests/N, or HOST/ORG/REPO/pulls/N.

Deliberately does not fetch a diff or the full comment timeline --
ansible-plaibook gets its diff from its own git clone/diff step
(resolve_target_pr.yml's "Clone the target repo" task) and only needs
enough narrative context here to avoid re-flagging something a human
reviewer already raised.

Output schema (identical shape regardless of platform, so the
rendering template needs no platform branching):
{
  "identifier": "PR #42" | "MR !42",
  "title": str, "author": str, "state": str,
  "source_branch": str, "target_branch": str, "url": str,
  "description": str,
  "ci_status": "passing" | "failing" | "running" | "none",
  "failing_checks": [{"name": str, "url": str}],
  "files_changed": [{"path": str, "status": str, "additions": int|null, "deletions": int|null}],
  "unresolved_comments": [{"author": str, "file": str, "line": int|str, "body": str}]
}

Usage:
    fetch_pr_context.py <URL>

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


def parse_url(url: str) -> tuple[str, str, str, int] | None:
    """Parse a fully-qualified PR/MR URL into (platform, hostname, project, number).

    Hostname/project charsets are deliberately restrictive (mirroring
    parse_pr_target.yml's own Ansible-side assert), not just "whatever
    matched" -- this script is invoked via a validated URL from that
    task today, but can also be run standalone outside the pipeline, so
    the same safe-to-embed guarantee needs to hold here independently.
    """
    m = re.match(r"^https?://github\.com/([A-Za-z0-9_.~-]+/[A-Za-z0-9_.~-]+)/pull/(\d+)$", url)
    if m:
        return "github", "github.com", m.group(1), int(m.group(2))
    m = re.match(r"^(https?)://([A-Za-z0-9_][A-Za-z0-9_.-]*(?::\d+)?)/([A-Za-z0-9_~][A-Za-z0-9_.~/-]*)/-/merge_requests/(\d+)$", url)
    if m:
        host = f"{m.group(1)}://{m.group(2)}" if m.group(1) == "http" else m.group(2)
        return "gitlab", host, m.group(3), int(m.group(4))
    m = re.match(r"^(https?)://([A-Za-z0-9_][A-Za-z0-9_.-]*(?::\d+)?)/([A-Za-z0-9_.~-]+/[A-Za-z0-9_.~-]+)/pulls?/(\d+)$", url)
    if m:
        host = f"{m.group(1)}://{m.group(2)}" if m.group(1) == "http" else m.group(2)
        return "gitea", host, m.group(3), int(m.group(4))
    return None


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 60) -> tuple[int, dict | list | None, dict[str, str]]:
    """Perform an HTTP GET request and return (status_code, parsed_json, response_headers)."""
    req_headers = {
        "User-Agent": "ansible-plaibook",
        "Accept": "application/json",
    }
    cf_id = os.environ.get("CF_ACCESS_CLIENT_ID") or os.environ.get("CLOUDFLARE_ACCESS_CLIENT_ID")
    cf_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET") or os.environ.get("CLOUDFLARE_ACCESS_CLIENT_SECRET")
    if cf_id and cf_secret:
        req_headers["CF-Access-Client-Id"] = cf_id
        req_headers["CF-Access-Client-Secret"] = cf_secret

    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None
            return status, data, resp_headers
    except urllib.error.HTTPError as e:
        resp_headers = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        try:
            raw = e.read().decode("utf-8")
            data = json.loads(raw)
        except Exception:
            data = None
        return e.code, data, resp_headers
    except Exception as e:
        print(f"Warning: GET {url} failed: {e}", file=sys.stderr)
        return 0, None, {}


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


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------

def fetch_gitlab(hostname: str, project: str, mr_iid: int) -> dict:
    token = get_gitlab_token()
    headers = {}
    if token:
        headers["PRIVATE-TOKEN"] = token

    encoded = urllib.parse.quote(project, safe="")
    base_url = f"https://{hostname}/api/v4"

    status, mr, _ = http_get(f"{base_url}/projects/{encoded}/merge_requests/{mr_iid}", headers=headers)
    if not mr or not isinstance(mr, dict) or status != 200:
        return {"error": f"could not fetch MR !{mr_iid} from {project} (HTTP {status})"}

    head_sha = (mr.get("diff_refs") or {}).get("head_sha") or mr.get("sha", "")

    # Fetch changed files
    status, changes, _ = http_get(f"{base_url}/projects/{encoded}/merge_requests/{mr_iid}/changes", headers=headers)
    raw_files = changes.get("changes", []) if isinstance(changes, dict) else []
    files_changed = []
    for f in raw_files:
        if f.get("new_file"):
            file_status = "Added"
        elif f.get("deleted_file"):
            file_status = "Deleted"
        elif f.get("renamed_file"):
            file_status = "Renamed"
        else:
            file_status = "Modified"
        files_changed.append({
            "path": f.get("new_path") or f.get("old_path") or "unknown",
            "status": file_status,
            "additions": None,
            "deletions": None,
        })

    # Fetch pipelines
    _, pipelines, _ = http_get(f"{base_url}/projects/{encoded}/merge_requests/{mr_iid}/pipelines?per_page=1", headers=headers)
    pipelines = pipelines if isinstance(pipelines, list) else []
    ci_status = "none"
    failing_checks = []
    if pipelines:
        pipe_status = pipelines[0].get("status", "unknown")
        _ci_status_map = {"success": "passing", "failed": "failing", "running": "running", "pending": "running"}
        ci_status = _ci_status_map.get(pipe_status, pipe_status)
        if pipe_status == "failed" and pipelines[0].get("id"):
            _, jobs, _ = http_get(f"{base_url}/projects/{encoded}/pipelines/{pipelines[0]['id']}/jobs", headers=headers)
            jobs = jobs if isinstance(jobs, list) else []
            failing_checks = [{"name": j.get("name", "unknown"), "url": j.get("web_url", "")} for j in jobs if j.get("status") == "failed"]

    # Fetch discussions with pagination
    unresolved_comments = []
    page = 1
    while page <= 10:
        _, discussions, resp_headers = http_get(
            f"{base_url}/projects/{encoded}/merge_requests/{mr_iid}/discussions?per_page=100&page={page}",
            headers=headers,
        )
        if not discussions or not isinstance(discussions, list):
            break
        for disc in discussions:
            notes = disc.get("notes", [])
            if not notes:
                continue
            first = notes[0]
            if not first.get("resolvable", False) or first.get("resolved", True):
                continue
            position = first.get("position", {})
            unresolved_comments.append({
                "author": (first.get("author") or {}).get("username", "unknown"),
                "file": position.get("new_path") or position.get("old_path") or "",
                "line": position.get("new_line") or position.get("old_line") or "",
                "body": first.get("body", ""),
            })
        if len(discussions) < 100:
            break
        page += 1

    return {
        "identifier": f"MR !{mr_iid}",
        "title": mr.get("title", "Unknown"),
        "author": (mr.get("author") or {}).get("username", "unknown"),
        "state": mr.get("state", "unknown"),
        "source_branch": mr.get("source_branch", "unknown"),
        "target_branch": mr.get("target_branch", "unknown"),
        "url": mr.get("web_url", ""),
        "description": mr.get("description", "") or "",
        "head_sha": head_sha,
        "ci_status": ci_status,
        "failing_checks": failing_checks,
        "files_changed": files_changed,
        "unresolved_comments": unresolved_comments,
    }


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def fetch_github(owner: str, repo: str, number: int) -> dict:
    token = get_github_token()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    base_url = f"https://api.github.com/repos/{owner}/{repo}"

    status, pr, _ = http_get(f"{base_url}/pulls/{number}", headers=headers)
    if not pr or not isinstance(pr, dict) or status != 200:
        return {"error": f"could not fetch PR #{number} from {owner}/{repo} (HTTP {status})"}

    head_sha = (pr.get("head") or {}).get("sha", "")

    # Fetch changed files with explicit pagination
    files_changed = []
    page = 1
    while page <= 30:  # Up to 3,000 files
        _, files_page, _ = http_get(f"{base_url}/pulls/{number}/files?per_page=100&page={page}", headers=headers)
        if not files_page or not isinstance(files_page, list):
            break
        for f in files_page:
            raw_status = f.get("status", "modified")
            status_map = {"added": "Added", "removed": "Deleted", "renamed": "Renamed", "modified": "Modified"}
            file_status = status_map.get(raw_status, "Modified")
            files_changed.append({
                "path": f.get("filename", "unknown"),
                "status": file_status,
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
            })
        if len(files_page) < 100:
            break
        page += 1

    # Fetch CI commit status / check runs
    ci_status = "none"
    failing_checks = []
    if head_sha:
        _, statuses_resp, _ = http_get(f"{base_url}/commits/{head_sha}/status", headers=headers)
        statuses = ((statuses_resp or {}).get("statuses") or []) if isinstance(statuses_resp, dict) else []

        _, check_runs_resp, _ = http_get(f"{base_url}/commits/{head_sha}/check-runs", headers=headers)
        check_runs = ((check_runs_resp or {}).get("check_runs") or []) if isinstance(check_runs_resp, dict) else []

        failing = []
        pending = []

        for s in statuses:
            state = s.get("state")
            if state in ("failure", "error"):
                failing.append({"name": s.get("context", "unknown"), "url": s.get("target_url", "")})
            elif state == "pending":
                pending.append(s)

        for c in check_runs:
            conclusion = c.get("conclusion")
            run_status = c.get("status")
            if conclusion in ("failure", "timed_out", "action_required"):
                failing.append({"name": c.get("name", "unknown"), "url": c.get("html_url", "")})
            elif run_status in ("in_progress", "queued"):
                pending.append(c)

        if failing:
            ci_status = "failing"
            failing_checks = failing
        elif pending:
            ci_status = "running"
        elif statuses or check_runs:
            ci_status = "passing"

    # Fetch review comments (unresolved/active discussions)
    unresolved_comments = []
    _, comments, _ = http_get(f"{base_url}/pulls/{number}/comments?per_page=100", headers=headers)
    if isinstance(comments, list):
        for c in comments:
            unresolved_comments.append({
                "author": (c.get("user") or {}).get("login", "ghost"),
                "file": c.get("path", ""),
                "line": c.get("line") or c.get("original_line") or "",
                "body": c.get("body", ""),
            })

    return {
        "identifier": f"PR #{pr.get('number', number)}",
        "title": pr.get("title", "Unknown"),
        "author": (pr.get("user") or {}).get("login", "ghost"),
        "state": pr.get("state", "unknown"),
        "source_branch": (pr.get("head") or {}).get("ref", "unknown"),
        "target_branch": (pr.get("base") or {}).get("ref", "unknown"),
        "url": pr.get("html_url", ""),
        "description": pr.get("body", "") or "",
        "head_sha": head_sha,
        "ci_status": ci_status,
        "failing_checks": failing_checks,
        "files_changed": files_changed,
        "unresolved_comments": unresolved_comments,
    }


# ---------------------------------------------------------------------------
# Gitea
# ---------------------------------------------------------------------------

def fetch_gitea(hostname: str, project: str, number: int) -> dict:
    token = get_gitea_token()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"

    scheme_host = hostname if (hostname.startswith("http://") or hostname.startswith("https://")) else f"https://{hostname}"
    base_url = f"{scheme_host}/api/v1/repos/{project}"

    status, pr, _ = http_get(f"{base_url}/pulls/{number}", headers=headers)
    if not pr or not isinstance(pr, dict) or status != 200:
        return {"error": f"could not fetch PR #{number} from {project} on {hostname} (HTTP {status})"}

    head_sha = (pr.get("head") or {}).get("sha", "")

    # Fetch changed files with pagination
    files_changed = []
    page = 1
    while page <= 30:
        _, files_page, _ = http_get(f"{base_url}/pulls/{number}/files?limit=50&page={page}", headers=headers)
        if not files_page or not isinstance(files_page, list):
            break
        for f in files_page:
            raw_status = f.get("status", "modified")
            status_map = {"added": "Added", "deleted": "Deleted", "renamed": "Renamed", "modified": "Modified"}
            file_status = status_map.get(raw_status, "Modified")
            files_changed.append({
                "path": f.get("filename", "unknown"),
                "status": file_status,
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
            })
        if len(files_page) < 50:
            break
        page += 1

    # Fetch commit status
    ci_status = "none"
    failing_checks = []
    if head_sha:
        _, statuses_resp, _ = http_get(f"{base_url}/commits/{head_sha}/statuses", headers=headers)
        statuses = statuses_resp if isinstance(statuses_resp, list) else []
        failing = []
        pending = []
        for s in statuses:
            state = s.get("status")
            if state in ("failure", "error"):
                failing.append({"name": s.get("context", "unknown"), "url": s.get("target_url", "")})
            elif state == "pending":
                pending.append(s)
        if failing:
            ci_status = "failing"
            failing_checks = failing
        elif pending:
            ci_status = "running"
        elif statuses:
            ci_status = "passing"

    # Fetch review comments
    unresolved_comments = []
    _, reviews_resp, _ = http_get(f"{base_url}/pulls/{number}/reviews", headers=headers)
    if isinstance(reviews_resp, list):
        for r in reviews_resp:
            review_id = r.get("id")
            if review_id:
                _, comments, _ = http_get(f"{base_url}/pulls/{number}/reviews/{review_id}/comments", headers=headers)
                if isinstance(comments, list):
                    for c in comments:
                        unresolved_comments.append({
                            "author": (c.get("user") or {}).get("login", "ghost"),
                            "file": c.get("path", ""),
                            "line": c.get("line") or c.get("old_line_num") or "",
                            "body": c.get("body", ""),
                        })

    return {
        "identifier": f"PR #{pr.get('number', number)}",
        "title": pr.get("title", "Unknown"),
        "author": (pr.get("user") or {}).get("login", "ghost"),
        "state": pr.get("state", "unknown"),
        "source_branch": (pr.get("head") or {}).get("ref", "unknown"),
        "target_branch": (pr.get("base") or {}).get("ref", "unknown"),
        "url": pr.get("html_url", ""),
        "description": pr.get("body", "") or "",
        "head_sha": head_sha,
        "ci_status": ci_status,
        "failing_checks": failing_checks,
        "files_changed": files_changed,
        "unresolved_comments": unresolved_comments,
    }


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    parsed = parse_url(sys.argv[1])
    if not parsed:
        print(f"Error: could not parse PR/MR URL: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)

    platform, hostname, project, number = parsed
    if platform == "github":
        owner, repo = project.split("/", 1)
        data = fetch_github(owner, repo, number)
    elif platform == "gitlab":
        data = fetch_gitlab(hostname, project, number)
    elif platform == "gitea":
        data = fetch_gitea(hostname, project, number)
    else:
        data = {"error": f"unsupported platform: {platform}"}

    if "error" in data:
        print(f"Error: {data['error']}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(data))


if __name__ == "__main__":
    main()

