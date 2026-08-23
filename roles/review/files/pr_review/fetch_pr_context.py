#!/usr/bin/env python3
"""Fetch PR/MR metadata, CI/pipeline status, and unresolved review
discussions for a single GitHub PR or GitLab MR, printed as one JSON
object on stdout — pure data-fetching, no presentation. The caller
(roles/review/tasks/resolve_target_pr.yml) renders that JSON through
roles/review/templates/pr_context.j2 to build review_context, the same
"Python fetches, Jinja presents" split resolve_target_commit.yml
already uses for commit_context.j2.

The caller already knows the platform and hostname from its own
target-parsing regexes and always hands this script a single,
unambiguous, fully-qualified URL -- github.com/OWNER/REPO/pull/N or
HOST/ORG/REPO/-/merge_requests/N. This script does no platform
auto-detection of its own on purpose: guessing from env vars or the
ambient git remote is exactly the failure mode a prior version of this
had (misfiring whenever the caller's shell had GITLAB_HOST set for
unrelated work) -- an unparseable URL should fail loudly here, not
silently guess wrong.

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

Requires: gh (GitHub) or glab (GitLab) CLI, already authenticated.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys


def parse_url(url: str) -> tuple[str, str, str, int] | None:
    """Parse a fully-qualified PR/MR URL into (platform, hostname, project, number).

    Hostname/project charsets are deliberately restrictive (mirroring
    parse_pr_target.yml's own Ansible-side assert), not just "whatever
    matched" -- this script is invoked via a validated URL from that
    task today, but can also be run standalone outside the pipeline, so
    the same safe-to-embed-in-a-glab/gh-argv guarantee needs to hold
    here independently, not just at the caller.
    """
    m = re.match(r"https?://github\.com/([A-Za-z0-9_.~-]+/[A-Za-z0-9_.~-]+)/pull/(\d+)", url)
    if m:
        return "github", "github.com", m.group(1), int(m.group(2))
    m = re.match(r"https?://([A-Za-z0-9_][A-Za-z0-9_.-]*)/([A-Za-z0-9_~][A-Za-z0-9_.~/-]*)/-/merge_requests/(\d+)", url)
    if m:
        return "gitlab", m.group(1), m.group(2), int(m.group(3))
    return None


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------

def glab_api(hostname: str, endpoint: str) -> dict | list:
    result = subprocess.run(["glab", "api", "--hostname", hostname, endpoint], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        err = result.stderr.strip() or f"exit {result.returncode}, no stderr output"
        print(f"Warning: glab api {endpoint}: {err}", file=sys.stderr)
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"Warning: could not parse response from {endpoint}", file=sys.stderr)
        return {}


def fetch_gitlab(hostname: str, project: str, mr_iid: int) -> dict:
    encoded = project.replace("/", "%2F")
    mr = glab_api(hostname, f"projects/{encoded}/merge_requests/{mr_iid}")
    if not mr:
        return {"error": f"could not fetch MR !{mr_iid} from {project}"}

    changes = glab_api(hostname, f"projects/{encoded}/merge_requests/{mr_iid}/changes")
    if not changes:
        # Unlike pipelines/discussions below, an empty result here is
        # never legitimate for a real MR (it always has at least one
        # changed file) -- glab_api's own warning already fired on the
        # underlying API failure, this just makes the caller-visible
        # symptom (an empty files_changed list) traceable back to it.
        print(f"Warning: could not fetch file changes for MR !{mr_iid}", file=sys.stderr)
    raw_files = changes.get("changes", []) if isinstance(changes, dict) else []
    files_changed = []
    for f in raw_files:
        if f.get("new_file"):
            status = "Added"
        elif f.get("deleted_file"):
            status = "Deleted"
        elif f.get("renamed_file"):
            status = "Renamed"
        else:
            status = "Modified"
        files_changed.append({
            "path": f.get("new_path") or f.get("old_path") or "unknown",
            "status": status,
            "additions": None,
            "deletions": None,
        })

    pipelines = glab_api(hostname, f"projects/{encoded}/merge_requests/{mr_iid}/pipelines?per_page=1")
    pipelines = pipelines if isinstance(pipelines, list) else []
    ci_status = "none"
    failing_checks = []
    if pipelines:
        status = pipelines[0].get("status", "unknown")
        # Falls through to the raw GitLab status string (e.g. "canceled",
        # "skipped", "manual") for anything not explicitly mapped --
        # pr_context.j2's own dict lookup uses .get() with this raw
        # value as its fallback, so an unmapped status renders as-is
        # rather than crashing the template.
        _ci_status_map = {"success": "passing", "failed": "failing", "running": "running", "pending": "running"}
        ci_status = _ci_status_map.get(status, status)
        if status == "failed" and pipelines[0].get("id"):
            jobs = glab_api(hostname, f"projects/{encoded}/pipelines/{pipelines[0]['id']}/jobs")
            jobs = jobs if isinstance(jobs, list) else []
            failing_checks = [{"name": j.get("name", "unknown"), "url": j.get("web_url", "")} for j in jobs if j.get("status") == "failed"]

    discussions = glab_api(hostname, f"projects/{encoded}/merge_requests/{mr_iid}/discussions?per_page=100")
    discussions = discussions if isinstance(discussions, list) else []
    unresolved_comments = []
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

    return {
        "identifier": f"MR !{mr_iid}",
        "title": mr.get("title", "Unknown"),
        "author": (mr.get("author") or {}).get("username", "unknown"),
        "state": mr.get("state", "unknown"),
        "source_branch": mr.get("source_branch", "unknown"),
        "target_branch": mr.get("target_branch", "unknown"),
        "url": mr.get("web_url", ""),
        "description": mr.get("description", ""),
        "ci_status": ci_status,
        "failing_checks": failing_checks,
        "files_changed": files_changed,
        "unresolved_comments": unresolved_comments,
    }


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

_UNRESOLVED_THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          comments(first: 1) {
            nodes { author { login } body path line }
          }
        }
      }
    }
  }
}
"""


def gh_cli(args: list[str]) -> str:
    result = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        err = result.stderr.strip()
        if err:
            print(f"Warning: gh {' '.join(args[:3])}: {err}", file=sys.stderr)
        return ""
    return result.stdout


def fetch_github(owner: str, repo: str, number: int) -> dict:
    fields = "title,number,url,author,headRefName,baseRefName,state,body,files,statusCheckRollup"
    raw = gh_cli(["pr", "view", str(number), "--repo", f"{owner}/{repo}", "--json", fields])
    if not raw:
        return {"error": f"could not fetch PR #{number} from {owner}/{repo}"}
    pr = json.loads(raw)

    checks = pr.get("statusCheckRollup", []) or []
    failing = [c for c in checks if c.get("conclusion") == "FAILURE" or c.get("state") == "FAILURE"]
    pending = [c for c in checks if c.get("status") in ("IN_PROGRESS", "QUEUED") or c.get("state") == "PENDING"]
    ci_status = "none" if not checks else "failing" if failing else "running" if pending else "passing"
    failing_checks = [{"name": c.get("name", c.get("context", "unknown")), "url": c.get("detailsUrl", c.get("targetUrl", ""))} for c in failing]

    # gh pr view --json files has no per-file status field at all
    # (confirmed directly against a real PR) -- GitHub's own REST API
    # does expose added/removed/modified/renamed per file, but the gh
    # CLI's files JSON doesn't surface it, so "Modified" is a known,
    # accepted simplification here, not an oversight.
    files_changed = [
        {"path": f.get("path", "unknown"), "status": "Modified", "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}
        for f in (pr.get("files") or [])
    ]

    threads_raw = gh_cli(["api", "graphql", "-f", f"query={_UNRESOLVED_THREADS_QUERY}",
                           "-f", f"owner={owner}", "-f", f"repo={repo}", "-F", f"number={number}"])
    unresolved_comments = []
    if threads_raw:
        try:
            data = json.loads(threads_raw)
            threads = ((data.get("data") or {}).get("repository") or {}).get("pullRequest", {}).get("reviewThreads", {}).get("nodes", [])
        except json.JSONDecodeError:
            print("Warning: could not parse GraphQL response for unresolved threads", file=sys.stderr)
            threads = []
        for thread in threads:
            if thread.get("isResolved"):
                continue
            comments = thread.get("comments", {}).get("nodes", [])
            if not comments:
                continue
            c = comments[0]
            unresolved_comments.append({
                "author": (c.get("author") or {}).get("login", "ghost"),
                "file": c.get("path", ""),
                "line": c.get("line", ""),
                "body": c.get("body", ""),
            })

    return {
        "identifier": f"PR #{pr.get('number', number)}",
        "title": pr.get("title", "Unknown"),
        "author": (pr.get("author") or {}).get("login", "ghost"),
        "state": pr.get("state", "unknown"),
        "source_branch": pr.get("headRefName", "unknown"),
        "target_branch": pr.get("baseRefName", "unknown"),
        "url": pr.get("url", ""),
        "description": pr.get("body", ""),
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
    else:
        data = fetch_gitlab(hostname, project, number)

    if "error" in data:
        print(f"Error: {data['error']}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(data))


if __name__ == "__main__":
    main()
