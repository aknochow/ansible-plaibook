#!/usr/bin/env python3
"""Wait for other GitHub checks, launch nothing itself, post the AAP result.

The controller job is launched by controller/launch_review.yml. This script
turns that job's plaibook_ci artifact into one check run named
"plaibook review" and a pull-request review with suggestion comments.

It never prints token values. A missing controller or a missing artifact
fails the check.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CHECK_NAME = "plaibook review"
KEYWORD = "/plai-review"
SUMMARY_MARKER = "<!-- plaibook-review-summary -->"
FINDING_MARKER = re.compile(r"<!-- plaibook-finding:([0-9a-f]+) -->")
ALLOWED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_SECRET_RE = re.compile(r"(gh[opsu]_|github_pat_)[A-Za-z0-9_]+")


def redact(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", text)[:500]


def is_review_command(body: str) -> bool:
    return body.lstrip().startswith(KEYWORD)


def comment_allowed(association: str, user_type: str) -> bool:
    if user_type == "Bot":
        return False
    return association in ALLOWED_ASSOCIATIONS


def check_conclusion(artifact: dict[str, Any]) -> str:
    if artifact.get("status") != "ok":
        return "failure"
    targets = artifact.get("targets") or []
    if not isinstance(targets, list) or not targets:
        return "failure"
    return "success" if all(t.get("verdict") == "READY_FOR_HUMAN_REVIEW" for t in targets) else "failure"


def other_work_pending(
    workflow_runs: list[dict[str, Any]],
    check_runs: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    required_workflows: set[str],
) -> list[str]:
    pending: list[str] = []
    seen: set[str] = set()
    for run in workflow_runs:
        name = str(run.get("name") or "")
        if name == CHECK_NAME:
            continue
        seen.add(name)
        if run.get("status") != "completed":
            pending.append(name)
    for check in check_runs:
        name = str(check.get("name") or "")
        if name == CHECK_NAME or name == "launch":
            continue
        if check.get("status") != "completed":
            pending.append(name)
    for status in statuses:
        context = str(status.get("context") or "")
        if context == CHECK_NAME:
            continue
        if status.get("state") == "pending":
            pending.append(context)
    for name in sorted(required_workflows - seen):
        pending.append(f"missing:{name}")
    return pending


def review_already_recorded(check_runs: list[dict[str, Any]]) -> bool:
    for check in check_runs:
        if check.get("name") == CHECK_NAME and check.get("status") in {"queued", "in_progress", "completed"}:
            return True
    return False


def plan_comments(
    existing: list[dict[str, Any]],
    new_comments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[int, dict[str, Any]]]]:
    by_fp: dict[str, dict[str, Any]] = {}
    for comment in existing:
        match = FINDING_MARKER.search(str(comment.get("body") or ""))
        if match:
            by_fp[match.group(1)] = comment
    create: list[dict[str, Any]] = []
    update: list[tuple[int, dict[str, Any]]] = []
    for comment in new_comments:
        previous = by_fp.get(str(comment.get("fingerprint") or ""))
        if previous is None:
            create.append(comment)
            continue
        if previous.get("body") != comment.get("body"):
            update.append((int(previous["id"]), comment))
    return create, update


def summary_body(artifact: dict[str, Any]) -> str:
    lines = ["plaibook review", ""]
    for target in artifact.get("targets") or []:
        lines.append(
            f"- {target.get('target') or '(target)'}: "
            f"{target.get('verdict') or 'unknown'} "
            f"(score {target.get('score')})"
        )
    if artifact.get("error"):
        lines.extend(["", str(artifact["error"])])
    if artifact.get("truncated"):
        lines.extend(["", "Some comments were truncated to fit the controller artifact."])
    lines.extend(["", f"Result file: {artifact.get('ci_result_file') or ''}", SUMMARY_MARKER])
    return "\n".join(lines)


class GitHub:
    def __init__(self, token: str, repo: str) -> None:
        self._token = token
        self.repo = repo

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        url = path if path.startswith("https://") else f"https://api.github.com{path}"
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "plaibook-review-check",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                parsed: Any = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"message": redact(raw)}
            return exc.code, parsed


def _event() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        return {}
    return json.loads(Path(path).read_text())


def _output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"{name}={value}")
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _state_dir() -> Path:
    path = Path(os.environ.get("PLAIBOOK_REVIEW_STATE", "/tmp/plaibook-review-state"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise SystemExit("GITHUB_REPOSITORY is missing or unsafe")
    return repo


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("GITHUB_TOKEN is unset")
    return token


def _required() -> set[str]:
    raw = os.environ.get("REQUIRED_WORKFLOWS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def resolve_pull(gh: GitHub, event: dict[str, Any], event_name: str) -> tuple[int, str] | None:
    if event_name == "workflow_dispatch":
        number = str((event.get("inputs") or {}).get("pr_number") or "")
        if not number.isdigit():
            raise SystemExit("workflow_dispatch requires a numeric pr_number")
        status, pull = gh.request("GET", f"/repos/{gh.repo}/pulls/{number}")
        if status != 200:
            raise SystemExit(f"could not read PR {number}: HTTP {status}")
        return int(number), str(pull["head"]["sha"])
    if event_name == "issue_comment":
        comment = event.get("comment") or {}
        user = comment.get("user") or {}
        if not comment_allowed(str(comment.get("author_association") or ""), str(user.get("type") or "")):
            print("ignoring comment: bot or not a collaborator")
            return None
        if not is_review_command(str(comment.get("body") or "")):
            print("ignoring comment: not the /plai-review command")
            return None
        issue = event.get("issue") or {}
        if "pull_request" not in issue:
            return None
        number = int(issue["number"])
        status, pull = gh.request("GET", f"/repos/{gh.repo}/pulls/{number}")
        if status != 200:
            raise SystemExit(f"could not read PR {number}: HTTP {status}")
        return number, str(pull["head"]["sha"])
    if event_name == "workflow_run":
        workflow_run = event.get("workflow_run") or {}
        if workflow_run.get("event") != "pull_request":
            print("ignoring workflow_run that is not from a pull request")
            return None
        sha = str(workflow_run.get("head_sha") or "")
        pulls = workflow_run.get("pull_requests") or []
        if pulls:
            return int(pulls[0]["number"]), sha
        status, found = gh.request("GET", f"/repos/{gh.repo}/commits/{sha}/pulls")
        if status == 200 and found:
            return int(found[0]["number"]), sha
        print("no pull request for this workflow_run")
        return None
    raise SystemExit(f"unsupported event {event_name}")


def _list_runs(gh: GitHub, sha: str) -> list[dict[str, Any]]:
    quoted = urllib.parse.quote(sha, safe="")
    status, body = gh.request("GET", f"/repos/{gh.repo}/actions/runs?head_sha={quoted}&per_page=100")
    if status != 200:
        raise SystemExit(f"listing workflow runs failed: HTTP {status}")
    return list(body.get("workflow_runs") or [])


def _list_checks(gh: GitHub, sha: str) -> list[dict[str, Any]]:
    status, body = gh.request("GET", f"/repos/{gh.repo}/commits/{sha}/check-runs?per_page=100")
    if status != 200:
        raise SystemExit(f"listing check runs failed: HTTP {status}")
    return list(body.get("check_runs") or [])


def _list_statuses(gh: GitHub, sha: str) -> list[dict[str, Any]]:
    status, body = gh.request("GET", f"/repos/{gh.repo}/commits/{sha}/statuses?per_page=100")
    if status != 200:
        raise SystemExit(f"listing statuses failed: HTTP {status}")
    return list(body)


def cmd_wait() -> None:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    gh = GitHub(_token(), _repo())
    resolved = resolve_pull(gh, _event(), event_name)
    if resolved is None:
        _output("skip", "true")
        return
    number, sha = resolved
    state = _state_dir()
    (state / "pr").write_text(json.dumps({"number": number, "sha": sha}))
    explicit = event_name in {"issue_comment", "workflow_dispatch"}
    if not explicit and review_already_recorded(_list_checks(gh, sha)):
        print(f"plaibook review already recorded for {sha[:12]}")
        _output("skip", "true")
        return
    required = _required()
    deadline = time.time() + int(os.environ.get("PLAIBOOK_WAIT_SECONDS", "2700"))
    stable = 0
    while time.time() < deadline:
        pending = other_work_pending(_list_runs(gh, sha), _list_checks(gh, sha), _list_statuses(gh, sha), required)
        if not pending:
            stable += 1
            if stable >= 2:
                print("other checks are complete")
                _output("skip", "false")
                return
        else:
            stable = 0
            print("waiting for: " + ", ".join(pending[:12]))
        time.sleep(int(os.environ.get("PLAIBOOK_WAIT_INTERVAL", "20")))
    _fail_check(gh, sha, "Timed out waiting for the other checks on this commit. No review ran.")
    raise SystemExit("timed out waiting for other checks")


def _fail_check(gh: GitHub, sha: str, message: str) -> None:
    status, _body = gh.request(
        "POST",
        f"/repos/{gh.repo}/check-runs",
        {
            "name": CHECK_NAME,
            "head_sha": sha,
            "status": "completed",
            "conclusion": "failure",
            "output": {"title": "plaibook review did not run", "summary": message},
        },
    )
    print(f"check create HTTP {status}")


def cmd_open_check() -> None:
    state = _state_dir()
    meta = json.loads((state / "pr").read_text())
    gh = GitHub(_token(), _repo())
    status, body = gh.request(
        "POST",
        f"/repos/{gh.repo}/check-runs",
        {
            "name": CHECK_NAME,
            "head_sha": meta["sha"],
            "status": "in_progress",
            "output": {
                "title": "plaibook review running",
                "summary": "Launching one controller job for this pull request.",
            },
        },
    )
    if status not in {200, 201}:
        raise SystemExit(f"could not open check run: HTTP {status}")
    (state / "check_id").write_text(str(body["id"]))
    print(f"opened check {body['id']}")


def cmd_launch() -> None:
    state = _state_dir()
    meta = json.loads((state / "pr").read_text())
    host = os.environ.get("CONTROLLER_HOST", "").strip()
    token_set = bool(os.environ.get("CONTROLLER_OAUTH_TOKEN", "").strip())
    print(f"controller_host_set={bool(host)} controller_token_set={token_set}")
    if not host or not token_set or "@" in host:
        (state / "launch_error").write_text(
            "AAP controller is not configured. Set the AAP_CONTROLLER_HOST variable "
            "and the AAP_CONTROLLER_TOKEN secret. No review ran."
        )
        raise SystemExit(2)
    root = Path(os.environ.get("PLAIBOOK_ROOT", "."))
    extra = root / ".plaibook-launch-extra.yml"
    owner, repo = _repo().split("/", 1)
    target = f"{owner}/{repo}/{meta['number']}"
    extra.write_text(
        "plaibook_review_target: "
        + json.dumps(target)
        + "\nplaibook_review_extra_vars:\n"
        + "  review_type: pr\n"
        + "  post_results: false\n"
        + "  review_require_ci_passing: false\n"
    )
    result_path = state / "ci-result.json"
    env = os.environ.copy()
    env["PLAIBOOK_CI_RESULT_PATH"] = str(result_path)
    env["ANSIBLE_COLLECTIONS_PATH"] = os.environ.get(
        "ANSIBLE_COLLECTIONS_PATH", str(root / ".galaxy-collections")
    )
    completed = subprocess.run(
        ["ansible-playbook", str(root / "controller" / "launch_review.yml"), "-e", f"@{extra}"],
        env=env,
        check=False,
    )
    if completed.returncode != 0 or not result_path.exists():
        (state / "launch_error").write_text(
            "The controller job did not return a plaibook_ci artifact. No review ran."
        )
        raise SystemExit(completed.returncode or 1)
    print(f"wrote {result_path.name}")


def _post_comments(gh: GitHub, number: int, sha: str, artifact: dict[str, Any]) -> None:
    status, existing = gh.request("GET", f"/repos/{gh.repo}/pulls/{number}/comments?per_page=100")
    if status != 200:
        existing = []
    comments: list[dict[str, Any]] = []
    for target in artifact.get("targets") or []:
        comments.extend(target.get("comments") or [])
    create, update = plan_comments(existing if isinstance(existing, list) else [], comments)
    for comment_id, comment in update:
        code, _body = gh.request(
            "PATCH",
            f"/repos/{gh.repo}/pulls/comments/{comment_id}",
            {"body": comment["body"]},
        )
        print(f"update comment {comment_id} HTTP {code}")
    for comment in create:
        code, body = gh.request(
            "POST",
            f"/repos/{gh.repo}/pulls/{number}/comments",
            {
                "body": comment["body"],
                "commit_id": sha,
                "path": comment["path"],
                "line": comment["line"],
                "side": comment.get("side") or "RIGHT",
            },
        )
        if code not in {200, 201}:
            print(f"inline comment skipped HTTP {code} path={comment['path']} line={comment['line']}")
            message = body.get("message") if isinstance(body, dict) else ""
            print(redact(str(message)))
    summary = summary_body(artifact)
    code, reviews = gh.request("GET", f"/repos/{gh.repo}/pulls/{number}/reviews?per_page=100")
    already = False
    if code == 200 and isinstance(reviews, list):
        already = any(SUMMARY_MARKER in str(review.get("body") or "") for review in reviews)
    if already:
        print("summary review already exists; inline comments were updated in place")
    else:
        code, _body = gh.request(
            "POST",
            f"/repos/{gh.repo}/pulls/{number}/reviews",
            {"commit_id": sha, "event": "COMMENT", "body": summary},
        )
        print(f"create summary review HTTP {code}")


def cmd_publish() -> None:
    state = _state_dir()
    meta = json.loads((state / "pr").read_text())
    check_id = (state / "check_id").read_text().strip()
    gh = GitHub(_token(), _repo())
    result_path = state / "ci-result.json"
    error_path = state / "launch_error"
    if result_path.exists():
        artifact = json.loads(result_path.read_text())
        conclusion = check_conclusion(artifact)
        title = "plaibook review"
        targets = artifact.get("targets") or []
        if targets:
            title = f"{targets[0].get('verdict') or 'unknown'} ({targets[0].get('score')})"
        _post_comments(gh, int(meta["number"]), str(meta["sha"]), artifact)
        summary = summary_body(artifact)
    else:
        conclusion = "failure"
        title = "plaibook review did not run"
        summary = error_path.read_text() if error_path.exists() else "No review ran."
    status, _body = gh.request(
        "PATCH",
        f"/repos/{_repo()}/check-runs/{check_id}",
        {
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": title, "summary": summary[:65000]},
        },
    )
    print(f"check {check_id} {conclusion} HTTP {status}")
    if conclusion != "success":
        raise SystemExit(1)


def main(argv: list[str]) -> None:
    command = argv[1] if len(argv) > 1 else ""
    if command == "wait":
        cmd_wait()
    elif command == "open-check":
        cmd_open_check()
    elif command == "launch":
        cmd_launch()
    elif command == "publish":
        cmd_publish()
    else:
        raise SystemExit("usage: github_review_check.py wait|open-check|launch|publish")


if __name__ == "__main__":
    main(sys.argv)
