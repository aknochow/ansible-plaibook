#!/usr/bin/env python3
"""GitHub check that runs after the other checks and posts a plai review.

The check name is ``plaibook review``. A comment that starts with
``/plai-review``, or ``workflow_dispatch`` from the default branch, runs
it again. A fork is reviewed only from that comment, and only when the
pull request author is OWNER, MEMBER, or COLLABORATOR.
``pull_request`` is refused: that event runs the workflow file
from the pull request. ``pull_request_target`` runs the file from the
base branch. This script does not call a controller. The workflow runs
``plai review`` and passes the JSON document in.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plaibook.finding_suggestions import (  # noqa: E402
    _has_replacement,
    comment_anchor,
    suggestion_errors,
    suggestion_replacement,
)

CHECK_NAME = "plaibook review"
REVIEW_KEYWORD = "/plai-review"
_COMMENT_BATCH = 30
SUMMARY_MARKER = "<!-- plaibook-review-summary -->"
FINDING_MARKER = re.compile(r"<!-- plaibook-finding:([0-9a-f]{16}) -->")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PASS_CONCLUSIONS = {"success", "skipped", "neutral"}
_BAD_SUITE = {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}
_ALLOWED_ASSOCIATION = {"OWNER", "MEMBER", "COLLABORATOR"}
# The cursor app on this repository opens a check suite and leaves it
# queued with zero runs. Waiting on that suite blocks the review forever.
# Every other empty, non-completed suite waits, including CircleCI and
# Buildkite. without_own_run already drops this workflow's own run.
_SUITES_THAT_NEVER_CREATE_RUNS = {"cursor"}
_REVIEW_APP = "plai-review[bot]"


def finding_key(finding: dict[str, Any]) -> str:
    description = re.sub(r"\s+", " ", str(finding.get("description") or "")).strip()[:120]
    raw = f"{finding.get('file')}:{finding.get('line')}:{finding.get('severity')}:{description}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _fence(text: str) -> str:
    ticks = 3
    while "`" * ticks in text:
        ticks += 1
    return f"{'`' * ticks}\n{text}\n{'`' * ticks}"


def finding_note(finding: dict[str, Any]) -> str:
    """Title and paragraphs for one finding. Shared by GitHub and GitLab markdown."""
    where = f"{finding.get('file')}:{finding.get('line')}"
    paragraphs = [
        f"**{finding.get('severity') or 'Finding'}** ({finding.get('lens') or 'review'}) `{where}`",
        str(finding.get("description") or "").strip(),
    ]
    fix = str(finding.get("fix") or "").strip()
    if fix:
        paragraphs.append(fix)
    return "\n\n".join(part for part in paragraphs if part)


def _indent_markdown(text: str) -> str:
    """Keep a paragraph inside a Markdown list item on GitHub and GitLab."""
    return "\n".join(f"   {line}" if line else "" for line in text.splitlines())


def findings_section(actions: list[dict[str, Any]]) -> str:
    """Number every finding. A blank line separates each item from the next."""
    items: list[str] = []
    number = 0
    for action in actions:
        note = str(action.get("note") or "")
        if not note and action.get("op") == "unanchored":
            note = str(action.get("text") or "")
        if not note.strip():
            continue
        number += 1
        paragraphs = [part.strip() for part in note.split("\n\n") if part.strip()]
        lines = [f"{number}. {paragraphs[0]}"]
        for paragraph in paragraphs[1:]:
            lines.extend(["", _indent_markdown(paragraph)])
        if action.get("suggested"):
            lines.extend(["", _indent_markdown("*See the suggested fix below.*")])
        items.append("\n".join(lines))
    return "\n\n".join(items)


def render_comment(finding: dict[str, Any]) -> str:
    """Build one review comment. The suggestion block is the replacement, never the explanation."""
    errors = suggestion_errors(finding)
    if errors:
        raise ValueError("; ".join(errors))
    replacement = suggestion_replacement(finding)
    parts = [
        f"**{finding.get('severity') or 'Finding'}** ({finding.get('lens') or 'review'})",
        "",
        str(finding.get("description") or "").strip(),
    ]
    evidence = str(finding.get("evidence") or "").strip()
    if evidence:
        parts.extend(["", "Evidence:", "", _fence(evidence)])
    parts.extend(["", "```suggestion", replacement or "", "```"])
    block = f"```suggestion\n{replacement}\n```"
    rendered_so_far = "\n".join(parts)
    if block not in rendered_so_far:
        raise ValueError(f"{finding.get('file')}:{finding.get('line')} suggestion block was not rendered")
    parts.append("")
    parts.append(f"<!-- plaibook-finding:{finding_key(finding)} -->")
    return "\n".join(parts).strip() + "\n"


def _github_login(item: dict[str, Any]) -> str:
    user = item.get("user")
    if not isinstance(user, dict):
        return ""
    return str(user.get("login") or "")


def _is_review_app(item: dict[str, Any]) -> bool:
    """True when the GitHub App that posts this review authored the object."""
    return _github_login(item) == _REVIEW_APP


def plan_comments(findings: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for comment in existing:
        if not _is_review_app(comment):
            continue
        match = FINDING_MARKER.search(comment.get("body") or "")
        if match:
            by_key[match.group(1)] = comment
    actions: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("evidence_status") == "refuted":
            continue
        note = finding_note(finding)
        # A bad replacement is listed in the summary and is not a suggestion.
        # Raising here would discard a finished review at post time.
        if suggestion_errors(finding) or not _has_replacement(finding):
            actions.append({"op": "unanchored", "text": note, "note": note, "suggested": False})
            continue
        body = render_comment(finding)
        key = finding_key(finding)
        previous = by_key.get(key)
        anchor = comment_anchor(finding)
        listed = {"note": note, "suggested": True}
        if previous is None:
            actions.append({"op": "create", "body": body, **anchor, **listed})
        elif (previous.get("body") or "") == body:
            actions.append(
                {
                    "op": "skip",
                    "id": previous.get("id"),
                    "review_id": previous.get("pull_request_review_id"),
                    "body": body,
                    **anchor,
                    **listed,
                }
            )
        else:
            actions.append({"op": "update", "id": previous.get("id"), "body": body, **anchor, **listed})
    return actions


# Exact GitHub check names for this caller workflow. The name is not enough:
# another workflow can publish a job with the same name. A run counts only
# when its Actions run is `.github/workflows/plai-review.yml`.
_REVIEW_WORKFLOW = ".github/workflows/plai-review.yml"
_REVIEW_CHECK_NAMES = frozenset({"plai", "plai / wait", "plai / review", CHECK_NAME})
_ACTIONS_RUN_ID = re.compile(r"/runs/(\d+)(?:/|$)")


def _actions_run_id(details_url: str) -> str:
    match = _ACTIONS_RUN_ID.search(details_url or "")
    return match.group(1) if match else ""


def review_run_ids(workflow_runs: list[dict[str, Any]]) -> set[str]:
    """Actions run ids whose workflow file is this repository's caller."""
    ids: set[str] = set()
    for run in workflow_runs:
        path = str(run.get("path") or "")
        if not path.endswith(_REVIEW_WORKFLOW):
            continue
        run_id = run.get("id")
        if run_id is not None:
            ids.add(str(run_id))
    return ids


def workflow_paths(workflow_runs: list[dict[str, Any]]) -> dict[str, str]:
    """Map an Actions run id to the workflow file that created it."""
    paths: dict[str, str] = {}
    for run in workflow_runs:
        run_id = run.get("id")
        if run_id is None:
            continue
        paths[str(run_id)] = str(run.get("path") or "")
    return paths


def _is_review_check(run: dict[str, Any], review_ids: set[str]) -> bool:
    """True for a job of this caller workflow, not a same-named job elsewhere."""
    if str(run.get("name") or "") not in _REVIEW_CHECK_NAMES:
        return False
    return _actions_run_id(str(run.get("details_url") or "")) in review_ids


def _review_suite_ids(check_runs: list[dict[str, Any]], review_ids: set[str]) -> set[Any]:
    return {
        (run.get("check_suite") or {}).get("id")
        for run in check_runs
        if _is_review_check(run, review_ids) and (run.get("check_suite") or {}).get("id") is not None
    }


def gate_state(
    check_runs: list[dict[str, Any]],
    check_suites: list[dict[str, Any]],
    review_ids: set[str] | None = None,
) -> str:
    """Return waiting, failed, or passed for the non-review checks on one SHA.

    ``skipped`` and ``neutral`` do not block. A failed check blocks.
    In-progress work waits. An empty suite that is not completed waits,
    except the cursor app, which stays queued with no runs on this
    repository. An empty completed suite fails unless its conclusion is
    success, skipped, or neutral. No other checks means passed, so a
    repository without other CI still gets a review.
    """
    trusted = review_ids or set()
    others = [run for run in check_runs if not _is_review_check(run, trusted)]
    review_suites = _review_suite_ids(check_runs, trusted)
    if any(run.get("status") != "completed" for run in others):
        return "waiting"
    for suite in check_suites:
        suite_id = suite.get("id")
        runs_in = [run for run in check_runs if (run.get("check_suite") or {}).get("id") == suite_id]
        if not runs_in:
            if suite_id in review_suites:
                continue
            app = suite.get("app") or {}
            slug = str(app.get("slug") or "") if isinstance(app, dict) else ""
            if suite.get("status") != "completed":
                if slug in _SUITES_THAT_NEVER_CREATE_RUNS:
                    continue
                return "waiting"
            if suite.get("conclusion") not in _PASS_CONCLUSIONS:
                return "failed"
            continue
        if suite_id in review_suites and all(_is_review_check(run, trusted) for run in runs_in):
            continue
        if suite.get("status") != "completed":
            return "waiting"
        if suite.get("conclusion") in _BAD_SUITE:
            return "failed"
    if not others:
        return "passed"
    if any(run.get("conclusion") not in _PASS_CONCLUSIONS for run in others):
        return "failed"
    return "passed"


def check_conclusion(result: dict[str, Any] | None) -> tuple[str, str, str]:
    """Success when the review ran and posted. The verdict is the review, not the job."""
    if not isinstance(result, dict) or not result.get("targets"):
        error = ""
        if isinstance(result, dict):
            error = str(result.get("error") or "").strip()
        if not error:
            error = "plai review did not write a run-scoped result."
        return ("failure", "No review result", error)
    targets = result.get("targets") or []
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict):
        return ("failure", "Expected one target", "This check reviews one pull request.")
    target = targets[0]
    verdict = str(target.get("verdict") or "")
    score = target.get("score")
    if score is None:
        score = target.get("score_overall")
    title = f"{verdict} ({score})" if score is not None else verdict or "Review"
    status = result.get("status")
    if status not in (None, "", "ok"):
        return ("failure", title, str(result.get("error") or "The review did not finish cleanly."))
    if verdict == "SKIPPED":
        return ("failure", title, str(target.get("skip_reason") or "The review did not run."))
    if verdict not in {"READY_FOR_HUMAN_REVIEW", "NEEDS_CHANGES"}:
        return ("failure", title, f"Verdict is {verdict or 'missing'}.")
    if verdict == "NEEDS_CHANGES":
        return (
            "success",
            title,
            "Review finished with NEEDS_CHANGES. The pull request review carries that verdict.",
        )
    return ("success", title, "Review finished with READY_FOR_HUMAN_REVIEW.")


def should_post(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict) or result.get("status") not in (None, "", "ok"):
        return False
    targets = result.get("targets") or []
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict):
        return False
    verdict = str(targets[0].get("verdict") or "")
    return verdict in {"READY_FOR_HUMAN_REVIEW", "NEEDS_CHANGES"}


def _run_detail(result: dict[str, Any]) -> str:
    """One line of run metadata for the review summary. Empty when absent."""
    parts: list[str] = []
    family = result.get("agent_family")
    if isinstance(family, str) and family.strip() and family != "unknown":
        parts.append(family.strip())
    models = result.get("models")
    if isinstance(models, list):
        names = [str(item).strip() for item in models if str(item).strip()]
        if names:
            parts.append(", ".join(names))
    agents = result.get("agents_dispatched")
    if isinstance(agents, int) and agents > 0:
        parts.append(f"{agents} agents")
    cost = result.get("cost_usd")
    if isinstance(cost, (int, float)):
        parts.append(f"${float(cost):.4f}")
    incoming = result.get("total_input_tokens")
    outgoing = result.get("total_output_tokens")
    if isinstance(incoming, int) and isinstance(outgoing, int) and (incoming or outgoing):
        parts.append(f"{incoming} in / {outgoing} out")
    return " · ".join(parts)


def summary_body(result: dict[str, Any], actions: list[dict[str, Any]]) -> str:
    target = (result.get("targets") or [{}])[0]
    score = target.get("score")
    if score is None:
        score = target.get("score_overall")
    lines = [
        f"plaibook review: **{target.get('verdict') or result.get('status')}**",
        "",
        f"Score: {score}",
    ]
    detail = _run_detail(result)
    if detail:
        lines.extend(["", detail])
    lines.extend(["", "Comment `/plai-review` on this pull request to run the review again."])
    section = findings_section(actions)
    if section:
        lines.extend(["", "## Findings", "", section])
    lines.extend(["", SUMMARY_MARKER])
    return "\n".join(lines)


def _head_full_name(pull: dict[str, Any]) -> str:
    head = pull.get("head") if isinstance(pull.get("head"), dict) else {}
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    return str(head_repo.get("full_name") or "")


def _foreign_head(pull: dict[str, Any], repo: str) -> dict[str, str] | None:
    if _head_full_name(pull) != repo:
        return {"action": "skip", "reason": "pull request head is not in this repository"}
    return None


def _author_association(pull: dict[str, Any]) -> str | None:
    """Return a non-blank association, or None when the field is absent."""
    if "author_association" not in pull:
        return None
    raw = pull.get("author_association")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _fork_skip(resolved: dict[str, str], pull: dict[str, Any], repo: str) -> dict[str, str] | None:
    """Skip a foreign head. A missing association has its own reason.

    pull_request_target skips every foreign head. An issue_comment reviews
    a foreign head only when the pull request author is OWNER, MEMBER, or
    COLLABORATOR. A missing or blank association is not one of those. That
    comment skips as ``pull request author_association is missing`` so an
    API omission is not reported as an ordinary outside fork. A present
    association outside that set keeps the foreign-head reason.
    """
    foreign = _foreign_head(pull, repo)
    if foreign is None:
        return None
    if resolved.get("trigger") != "issue_comment":
        return foreign
    association = _author_association(pull)
    if association in _ALLOWED_ASSOCIATION:
        return None
    if association is None:
        return {"action": "skip", "reason": "pull request author_association is missing"}
    return foreign


def _resolve_target_pull(event_name: str, event: dict[str, Any], repo: str) -> dict[str, str]:
    pull = event.get("pull_request") or {}
    head = pull.get("head") or {}
    foreign = _foreign_head(pull, repo)
    if foreign:
        return foreign
    # Publish and freshness use head.sha. That SHA is the commit under
    # review. It is not checked out. The gate reads the head when it has
    # the other check runs, which is where pull_request checks are
    # attached in this repository.
    return {
        "action": "review",
        "repo": repo,
        "pr": str(pull.get("number") or ""),
        "sha": str(head.get("sha") or ""),
        "trigger": event_name,
    }


def _dispatch_from_default_branch(event: dict[str, Any]) -> bool:
    ref = os.environ.get("GITHUB_REF") or ""
    default = str((event.get("repository") or {}).get("default_branch") or "")
    return bool(default) and ref == f"refs/heads/{default}"


def resolve_event(event_name: str, event: dict[str, Any], repo: str) -> dict[str, str]:
    # pull_request evaluates the workflow file from the pull request branch
    # and still receives secrets for a same-repository head. Refuse it.
    if event_name == "pull_request":
        return {
            "action": "skip",
            "reason": "pull_request runs workflow code from the pull request branch",
        }
    if event_name == "pull_request_target":
        return _resolve_target_pull(event_name, event, repo)
    if event_name == "workflow_dispatch":
        if not _dispatch_from_default_branch(event):
            return {"action": "skip", "reason": "workflow_dispatch runs only from the default branch"}
        pr = str((event.get("inputs") or {}).get("pr_number") or "").strip()
        if not pr.isdigit():
            return {"action": "skip", "reason": "workflow_dispatch requires a numeric pr_number"}
        return {"action": "review", "repo": repo, "pr": pr, "sha": "", "trigger": event_name}
    if event_name == "issue_comment":
        issue = event.get("issue") or {}
        comment = event.get("comment") or {}
        user = comment.get("user") or {}
        login = str(user.get("login") or "")
        body = str(comment.get("body") or "").lstrip()
        if not issue.get("pull_request"):
            return {"action": "skip", "reason": "comment is not on a pull request"}
        if user.get("type") == "Bot" or login.endswith("[bot]"):
            return {"action": "skip", "reason": "ignoring bot comment"}
        if str(comment.get("author_association") or "") not in _ALLOWED_ASSOCIATION:
            return {"action": "skip", "reason": "comment author cannot start a review"}
        if not body.startswith(REVIEW_KEYWORD):
            return {"action": "skip", "reason": "comment has no review keyword"}
        return {
            "action": "review",
            "repo": repo,
            "pr": str(issue.get("number") or ""),
            "sha": "",
            "trigger": event_name,
        }
    return {"action": "skip", "reason": f"unsupported event {event_name}"}


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        raise RuntimeError("GITHUB_TOKEN is unset")
    return token


def _request(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, Any, str]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ansible-plaibook-plaibook-review",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            link = response.headers.get("Link") or ""
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, raw, ""
    if not raw:
        return status, None, link
    try:
        return status, json.loads(raw), link
    except json.JSONDecodeError:
        return status, raw, link


def _next_link(link_header: str) -> str:
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' in section:
            return section.split(";")[0].strip().strip("<>")
    return ""


def _get_all(url: str, key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while url:
        status, payload, link = _request("GET", url)
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"GitHub API {status} for {url.split('?', 1)[0]}")
        batch = payload.get(key) or []
        if not isinstance(batch, list):
            raise RuntimeError(f"GitHub API {key} was not a list")
        items.extend(item for item in batch if isinstance(item, dict))
        url = _next_link(link)
    return items


def _api(repo: str) -> str:
    if not _REPO.fullmatch(repo):
        raise RuntimeError("repository must be owner/name")
    return f"https://api.github.com/repos/{repo}"


def _merge_sha(pull: dict[str, Any]) -> str:
    sha = str(pull.get("merge_commit_sha") or "")
    return sha if _SHA.fullmatch(sha) else ""


def fill_pull_request(resolved: dict[str, str]) -> dict[str, str]:
    repo = resolved["repo"]
    pr = resolved.get("pr") or ""
    sha = resolved.get("sha") or ""
    merge_sha = ""
    if pr.isdigit():
        status, payload, _link = _request("GET", f"{_api(repo)}/pulls/{pr}")
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"unable to read pull request {pr}")
        foreign = _fork_skip(resolved, payload, repo)
        if foreign:
            return foreign
        merge_sha = _merge_sha(payload)
        if not _SHA.fullmatch(sha):
            sha = str((payload.get("head") or {}).get("sha") or "")
    elif _SHA.fullmatch(sha):
        status, payload, _link = _request("GET", f"{_api(repo)}/commits/{sha}/pulls")
        if status != 200 or not isinstance(payload, list):
            raise RuntimeError("unable to map the commit to a pull request")
        open_prs = [item for item in payload if isinstance(item, dict) and item.get("state") == "open"]
        chosen = open_prs or [item for item in payload if isinstance(item, dict)]
        if not chosen:
            return {"action": "skip", "reason": "no pull request for this SHA"}
        foreign = _fork_skip(resolved, chosen[0], repo)
        if foreign:
            return foreign
        merge_sha = _merge_sha(chosen[0])
        pr = str(chosen[0].get("number") or "")
    if not pr.isdigit() or not _SHA.fullmatch(sha):
        raise RuntimeError("pull request number or head SHA is missing")
    return {**resolved, "pr": pr, "sha": sha, "merge_sha": merge_sha, "action": "review"}


def reviewed_sha_is_current(repo: str, pr: str, sha: str) -> bool:
    """True when the pull request head is still the commit this run reviewed."""
    status, payload, _link = _request("GET", f"{_api(repo)}/pulls/{pr}")
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"unable to read pull request {pr}")
    current = str((payload.get("head") or {}).get("sha") or "")
    return _SHA.fullmatch(current) is not None and current == sha


def _run_sort_key(run: dict[str, Any]) -> tuple[int, str]:
    try:
        run_id = int(run.get("id") or 0)
    except (TypeError, ValueError):
        run_id = 0
    return (run_id, str(run.get("started_at") or ""))


def _attempt_key(run: dict[str, Any], paths: dict[str, str]) -> tuple[str, ...]:
    """Identity of one check. Reruns share a workflow file. Same display names do not."""
    name = str(run.get("name") or "")
    actions_id = _actions_run_id(str(run.get("details_url") or ""))
    path = paths.get(actions_id, "")
    if path:
        return (path, name)
    suite_id = (run.get("check_suite") or {}).get("id")
    if suite_id is not None:
        return ("suite", str(suite_id), name)
    return ("name", name)


def latest_check_runs(
    check_runs: list[dict[str, Any]], workflow_paths_by_run: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Keep the newest attempt of one check. A different workflow keeps its own run."""
    paths = workflow_paths_by_run or {}
    latest: dict[tuple[str, ...], dict[str, Any]] = {}
    order: list[tuple[str, ...]] = []
    for run in check_runs:
        key = _attempt_key(run, paths)
        previous = latest.get(key)
        if previous is None:
            order.append(key)
            latest[key] = run
            continue
        if _run_sort_key(run) >= _run_sort_key(previous):
            latest[key] = run
    return [latest[key] for key in order]


def without_own_run(
    check_runs: list[dict[str, Any]], check_suites: list[dict[str, Any]], run_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop the current Actions run so the gate does not wait on itself."""
    if not run_id:
        return check_runs, check_suites
    needle = f"/runs/{run_id}/"
    own_suites = {
        (run.get("check_suite") or {}).get("id")
        for run in check_runs
        if needle in str(run.get("details_url") or "")
    }
    own_suites.discard(None)
    runs = [run for run in check_runs if needle not in str(run.get("details_url") or "")]
    suites = [suite for suite in check_suites if suite.get("id") not in own_suites]
    return runs, suites


def _workflow_runs_for_sha(repo: str, sha: str) -> list[dict[str, Any]]:
    return _get_all(f"{_api(repo)}/actions/runs?head_sha={sha}&per_page=100", "workflow_runs")


def other_check_count(repo: str, sha: str, ignore_run_id: str = "") -> int:
    """How many check runs on this commit belong to other workflows."""
    if not _SHA.fullmatch(sha):
        return 0
    runs = _get_all(f"{_api(repo)}/commits/{sha}/check-runs?per_page=100", "check_runs")
    runs, _suites = without_own_run(runs, [], ignore_run_id)
    workflow_runs = _workflow_runs_for_sha(repo, sha)
    trusted = review_run_ids(workflow_runs)
    others = [run for run in runs if not _is_review_check(run, trusted)]
    return len(latest_check_runs(others, workflow_paths(workflow_runs)))


def commits_to_gate(head_sha: str, head_count: int, merge_sha: str, merge_count: int) -> list[str]:
    """Commits whose checks and suites the gate must read.

    A valid merge SHA is always included, even before it has check runs.
    A queued suite can exist on that commit with no runs yet. Counts are
    not used to drop a commit; ``gate`` inspects suites either way.
    """
    del head_count, merge_count
    commits: list[str] = []
    if _SHA.fullmatch(head_sha):
        commits.append(head_sha)
    if merge_sha and merge_sha != head_sha and _SHA.fullmatch(merge_sha):
        commits.append(merge_sha)
    return commits


def combine_gate_states(states: list[str]) -> str:
    """Fail if either commit failed. Wait if either is still running."""
    if not states or "waiting" in states:
        if states and "failed" in states:
            return "failed"
        return "waiting"
    if "failed" in states:
        return "failed"
    return "passed"


def select_gate_sha(head_sha: str, head_count: int, merge_sha: str, merge_count: int) -> str:
    """Return the only commit that has other checks.

    Both commits must be gated together when each has checks. This helper
    is for the single-commit case.
    """
    commits = commits_to_gate(head_sha, head_count, merge_sha, merge_count)
    if len(commits) != 1:
        raise RuntimeError("gate the head and merge commits together")
    return commits[0]


def resolve_gate_sha(repo: str, head_sha: str, merge_sha: str = "", ignore_run_id: str = "") -> str:
    if not _SHA.fullmatch(head_sha):
        raise RuntimeError("head SHA must be 40 hex characters")
    head_count = other_check_count(repo, head_sha, ignore_run_id)
    merge_count = 0
    if merge_sha and merge_sha != head_sha and _SHA.fullmatch(merge_sha):
        merge_count = other_check_count(repo, merge_sha, ignore_run_id)
    return select_gate_sha(head_sha, head_count, merge_sha, merge_count)


def gate_commits(repo: str, head_sha: str, merge_sha: str = "", ignore_run_id: str = "") -> str:
    """Gate every commit that carries other checks."""
    if not _SHA.fullmatch(head_sha):
        raise RuntimeError("head SHA must be 40 hex characters")
    head_count = other_check_count(repo, head_sha, ignore_run_id)
    merge_count = 0
    if merge_sha and merge_sha != head_sha and _SHA.fullmatch(merge_sha):
        merge_count = other_check_count(repo, merge_sha, ignore_run_id)
    commits = commits_to_gate(head_sha, head_count, merge_sha, merge_count)
    return combine_gate_states([gate(repo, sha, ignore_run_id) for sha in commits])


def gate(repo: str, sha: str, ignore_run_id: str = "") -> str:
    if not _SHA.fullmatch(sha):
        raise RuntimeError("head SHA must be 40 hex characters")
    base = _api(repo)
    runs = _get_all(f"{base}/commits/{sha}/check-runs?per_page=100", "check_runs")
    suites = _get_all(f"{base}/commits/{sha}/check-suites?per_page=100", "check_suites")
    runs, suites = without_own_run(runs, suites, ignore_run_id)
    workflow_runs = _workflow_runs_for_sha(repo, sha)
    runs = latest_check_runs(runs, workflow_paths(workflow_runs))
    return gate_state(runs, suites, review_run_ids(workflow_runs))


def open_check(repo: str, sha: str, pr: str) -> int:
    status, payload, _link = _request(
        "POST",
        f"{_api(repo)}/check-runs",
        {
            "name": CHECK_NAME,
            "head_sha": sha,
            "status": "in_progress",
            "details_url": f"https://github.com/{repo}/pull/{pr}",
            "output": {"title": "Review in progress", "summary": "Running plai review."},
        },
    )
    if status not in (200, 201) or not isinstance(payload, dict) or not payload.get("id"):
        raise RuntimeError(f"unable to open {CHECK_NAME} check ({status})")
    return int(payload["id"])


def complete_check(repo: str, check_id: int, conclusion: str, title: str, summary: str) -> None:
    status, _payload, _link = _request(
        "PATCH",
        f"{_api(repo)}/check-runs/{check_id}",
        {
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": title[:120], "summary": summary[:60000]},
        },
    )
    if status != 200:
        raise RuntimeError(f"unable to complete {CHECK_NAME} check ({status})")


def _list_review_comments(repo: str, pr: str) -> list[dict[str, Any]]:
    url = f"{_api(repo)}/pulls/{pr}/comments?per_page=100"
    comments: list[dict[str, Any]] = []
    while url:
        status, payload, link = _request("GET", url)
        if status != 200 or not isinstance(payload, list):
            raise RuntimeError(f"unable to list review comments ({status})")
        comments.extend(item for item in payload if isinstance(item, dict))
        url = _next_link(link)
    return comments


def review_event(verdict: str) -> str:
    """NEEDS_CHANGES is a pull request review. Other posted verdicts stay comments."""
    if verdict == "NEEDS_CHANGES":
        return "REQUEST_CHANGES"
    return "COMMENT"


def _desired_review_state(event: str) -> str:
    if event == "REQUEST_CHANGES":
        return "CHANGES_REQUESTED"
    if event == "COMMENT":
        return "COMMENTED"
    raise RuntimeError(f"unsupported review event {event}")


def _without_run_stats(body: str) -> str:
    """Drop the cost and token line. Those counts change on every rerun."""
    kept = [
        line
        for line in body.splitlines()
        if not (" · " in line and ("$" in line or " in / " in line))
    ]
    return "\n".join(kept)


def review_matches(review: dict[str, Any], body: str, event: str, sha: str) -> bool:
    """True when this App already posted this verdict on this commit.

    Cost and token counts are not part of the identity. A review written
    by anyone else is not reused, updated, or retired.
    """
    if not _is_review_app(review):
        return False
    return (
        _without_run_stats(str(review.get("body") or "")) == _without_run_stats(body)
        and review.get("state") == _desired_review_state(event)
        and review.get("commit_id") == sha
    )


def review_payload(
    sha: str, body: str, event: str, comments: list[dict[str, Any]]
) -> dict[str, Any]:
    payload: dict[str, Any] = {"commit_id": sha, "body": body, "event": event}
    if comments:
        payload["comments"] = comments
    return payload


def _review_comment(action: dict[str, Any]) -> dict[str, Any]:
    comment: dict[str, Any] = {
        "path": action["path"],
        "line": action["line"],
        "side": "RIGHT",
        "body": action["body"],
    }
    start_line = action.get("start_line")
    if isinstance(start_line, int) and 1 <= start_line < int(action["line"]):
        comment["start_line"] = start_line
        comment["start_side"] = "RIGHT"
    return comment


def partition_actions(
    actions: list[dict[str, Any]], *, rehome: bool
) -> tuple[list[dict[str, Any]], list[int], list[dict[str, Any]]]:
    """Split a plan into review comments, comments to retire, and in-place updates.

    A new review owns its inline comments. Identical comments from an older
    review are included again and retired after the new review is accepted.
    """
    comments: list[dict[str, Any]] = []
    retire: list[int] = []
    updates: list[dict[str, Any]] = []
    for action in actions:
        op = action.get("op")
        if op == "update" and not rehome:
            updates.append(action)
            continue
        if op == "create" or (rehome and op in {"skip", "update"}):
            comments.append(_review_comment(action))
            if op != "create" and action.get("id") is not None:
                retire.append(int(action["id"]))
    return comments, retire, updates


def publish_review(repo: str, pr: str, sha: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    verdict = ""
    targets = result.get("targets") or []
    if isinstance(targets, list) and targets and isinstance(targets[0], dict):
        raw_findings = targets[0].get("findings") or []
        if isinstance(raw_findings, list):
            findings = [item for item in raw_findings if isinstance(item, dict)]
        verdict = str(targets[0].get("verdict") or "")
    existing = _list_review_comments(repo, pr)
    actions = plan_comments(findings, existing)
    event = review_event(verdict)
    body = summary_body(result, actions)
    canonical = _canonical_reviews(repo, pr, body, event, sha)
    rehome = not canonical
    has_new = any(action.get("op") == "create" for action in actions)
    if canonical and not has_new:
        _apply_updates(repo, [action for action in actions if action.get("op") == "update"])
        _retire_replaced_comments(repo, existing, canonical)
        return actions
    comments, retire, updates = partition_actions(actions, rehome=rehome)
    if rehome:
        retire = _retire_absent_findings(actions, existing, retire)
    _apply_updates(repo, updates)
    _submit_review(repo, pr, sha, body, event, comments, actions, result)
    _retire_comments(repo, retire)
    return actions


def _apply_updates(repo: str, actions: list[dict[str, Any]]) -> None:
    base = _api(repo)
    for action in actions:
        status, _payload, _link = _request(
            "PATCH",
            f"{base}/pulls/comments/{action['id']}",
            {"body": action["body"]},
        )
        if status != 200:
            raise RuntimeError(f"unable to update review comment ({status})")


def _error_text(payload: Any) -> str:
    if isinstance(payload, dict):
        parts = [str(payload.get("message") or "")]
        for err in payload.get("errors") or []:
            if isinstance(err, dict):
                parts.append(str(err.get("message") or err.get("code") or ""))
            else:
                parts.append(str(err))
        return " ".join(part for part in parts if part)
    return str(payload or "")


def _comment_rejection(payload: Any) -> str:
    """Classify a 422 from the review comments. line, limit, or other."""
    lowered = _error_text(payload).lower()
    if any(word in lowered for word in ("line", "diff", "position", "path", "thread")):
        return "line"
    if "too many" in lowered or "comment limit" in lowered:
        return "limit"
    return "other"


def _post_review(repo: str, pr: str, payload: dict[str, Any]) -> str:
    status, body, _link = _request("POST", f"{_api(repo)}/pulls/{pr}/reviews", payload)
    if status in (200, 201):
        return "ok"
    if status == 422 and payload.get("comments"):
        kind = _comment_rejection(body)
        if kind in {"line", "limit"}:
            return kind
        raise RuntimeError(f"unable to post the pull request review ({status}): {_error_text(body)}")
    raise RuntimeError(f"unable to post the pull request review ({status}): {_error_text(body)}")


def _submit_review(
    repo: str,
    pr: str,
    sha: str,
    body: str,
    event: str,
    comments: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    result: dict[str, Any],
) -> None:
    """Post the review, splitting inline comments into batches GitHub accepts."""
    if not comments:
        outcome = _post_review(repo, pr, review_payload(sha, body, event, []))
        if outcome != "ok":
            raise RuntimeError(f"unable to post the pull request review ({outcome})")
        return
    pending = list(comments)
    size = _COMMENT_BATCH
    first = True
    while pending:
        batch = pending[:size]
        use_event = event if first else "COMMENT"
        use_body = body if first else "plaibook review suggestions, continued.\n\n" + SUMMARY_MARKER
        outcome = _post_review(repo, pr, review_payload(sha, use_body, use_event, batch))
        if outcome == "ok":
            pending = pending[size:]
            first = False
            continue
        if outcome == "limit":
            if size <= 1:
                raise RuntimeError("GitHub rejected a single inline comment as too many comments")
            size = max(1, size // 2)
            continue
        if not first:
            raise RuntimeError("a continued suggestion batch was rejected for line location")
        fallback = [
            {"op": "unanchored", "text": action["body"]} if action.get("op") == "create" else action
            for action in actions
        ]
        fallback_body = summary_body(result, fallback)
        outcome = _post_review(repo, pr, {"commit_id": sha, "body": fallback_body, "event": event})
        if outcome != "ok":
            raise RuntimeError("unable to post the summary after inline comments were rejected")
        return


def _inline_markers(actions: list[dict[str, Any]]) -> set[str]:
    """Markers for findings that still post an inline suggestion."""
    markers: set[str] = set()
    for action in actions:
        if not action.get("suggested"):
            continue
        match = FINDING_MARKER.search(str(action.get("body") or ""))
        if match:
            markers.add(match.group(1))
    return markers


def _retire_absent_findings(
    actions: list[dict[str, Any]], existing: list[dict[str, Any]], retire: list[int]
) -> list[int]:
    """Retire bot comments that are no longer an inline suggestion.

    A finding that stays in the summary without a committable replacement
    does not keep its old suggestion comment.
    """
    markers = _inline_markers(actions)
    seen = set(retire)
    for comment in existing:
        if not _is_review_app(comment):
            continue
        match = FINDING_MARKER.search(comment.get("body") or "")
        if not match or match.group(1) in markers or comment.get("id") is None:
            continue
        comment_id = int(comment["id"])
        if comment_id in seen:
            continue
        retire.append(comment_id)
        seen.add(comment_id)
    return retire


def _retire_comments(repo: str, comment_ids: list[int]) -> None:
    for comment_id in comment_ids:
        status, _payload, _link = _request("DELETE", f"{_api(repo)}/pulls/comments/{comment_id}")
        if status not in (200, 204):
            print(f"unable to retire previous review comment {comment_id} ({status})", file=sys.stderr)


def _retire_replaced_comments(
    repo: str, existing: list[dict[str, Any]], canonical: list[dict[str, Any]]
) -> None:
    """Drop finding comments that were replaced by the review already on this commit."""
    canonical_ids = {int(review["id"]) for review in canonical if review.get("id") is not None}
    kept: set[str] = set()
    for comment in existing:
        if not _is_review_app(comment):
            continue
        if comment.get("pull_request_review_id") not in canonical_ids:
            continue
        match = FINDING_MARKER.search(comment.get("body") or "")
        if match:
            kept.add(match.group(1))
    stale: list[int] = []
    for comment in existing:
        if not _is_review_app(comment):
            continue
        match = FINDING_MARKER.search(comment.get("body") or "")
        if not match or match.group(1) not in kept:
            continue
        if comment.get("pull_request_review_id") in canonical_ids or comment.get("id") is None:
            continue
        stale.append(int(comment["id"]))
    _retire_comments(repo, stale)


def _canonical_reviews(repo: str, pr: str, body: str, event: str, sha: str) -> list[dict[str, Any]]:
    url = f"{_api(repo)}/pulls/{pr}/reviews?per_page=100"
    found: list[dict[str, Any]] = []
    while url:
        status, payload, link = _request("GET", url)
        if status != 200 or not isinstance(payload, list):
            raise RuntimeError(f"unable to list reviews ({status})")
        for review in payload:
            if isinstance(review, dict) and review_matches(review, body, event, sha):
                found.append(review)
        url = _next_link(link)
    return found


def _write_output(values: dict[str, str]) -> None:
    for key, value in values.items():
        if not re.fullmatch(r"[A-Za-z0-9_.:/@ -]*", value):
            raise RuntimeError(f"refusing to export {key}")
        print(f"{key}={value}")


def _load_event(args: argparse.Namespace) -> tuple[str, dict[str, Any], str]:
    event_name = args.event_name or os.environ.get("GITHUB_EVENT_NAME") or ""
    path = args.event_path or os.environ.get("GITHUB_EVENT_PATH") or ""
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY") or ""
    if not path:
        raise RuntimeError("GITHUB_EVENT_PATH is unset")
    event = json.loads(open(path, encoding="utf-8").read())
    if not isinstance(event, dict):
        raise RuntimeError("GitHub event was not an object")
    return event_name, event, repo


def _cmd_resolve(args: argparse.Namespace) -> int:
    try:
        event_name, event, repo = _load_event(args)
        resolved = resolve_event(event_name, event, repo)
        if resolved.get("action") == "review":
            resolved = fill_pull_request(resolved)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if resolved.get("action") != "review":
        _write_output({"action": "skip"})
        print(resolved.get("reason") or "skip", file=sys.stderr)
        return 0
    _write_output(
        {
            "action": "review",
            "repo": resolved["repo"],
            "pr": resolved["pr"],
            "sha": resolved["sha"],
            "merge_sha": resolved.get("merge_sha") or "",
            "trigger": resolved["trigger"],
        }
    )
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    try:
        merge = getattr(args, "merge", "") or ""
        if merge:
            state = gate_commits(args.repo, args.sha, merge, ignore_run_id=args.ignore_run_id)
        else:
            state = gate(args.repo, args.sha, ignore_run_id=args.ignore_run_id)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(state)
    return {"passed": 0, "waiting": 10, "failed": 20}[state]


def _cmd_open(args: argparse.Namespace) -> int:
    try:
        print(open_check(args.repo, args.sha, args.pr))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _cmd_fail(args: argparse.Namespace) -> int:
    try:
        check_id = int(args.check_id) if args.check_id else open_check(args.repo, args.sha, args.pr)
        complete_check(args.repo, check_id, "failure", args.title, args.summary)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


def result_for_publish(path: str, review_rc: object) -> dict[str, Any] | None:
    present = bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0
    result = _load_result(path)
    if review_rc not in (0, "0"):
        error = f"plai review exited {review_rc}"
        if isinstance(result, dict):
            detail = str(result.get("error") or "").strip()
            if detail:
                error = f"{error}: {detail}"
        return {"status": "failed", "error": error, "targets": []}
    if present and result is None:
        return {"status": "failed", "error": "plai review result is not valid JSON", "targets": []}
    return result


def _load_result(path: str) -> dict[str, Any] | None:
    if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.loads(handle.read())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def failure_notice(result: dict[str, Any] | None, review_rc: str = "") -> str:
    """Review body for a run that did not produce postable findings."""
    error = ""
    if isinstance(result, dict):
        error = str(result.get("error") or "").strip()
    if not error:
        error = f"plai review exited {review_rc or 'nonzero'} without a result."
    if len(error) > 12000:
        error = error[:12000] + "\n…"
    lines = ["plaibook review: **failed**", "", error]
    if isinstance(result, dict):
        detail = _run_detail(result)
        if detail:
            lines.extend(["", detail])
    lines.extend(["", SUMMARY_MARKER])
    return "\n".join(lines)


def _cmd_publish(args: argparse.Namespace) -> int:
    result = result_for_publish(args.result, args.review_rc)
    conclusion, title, summary = check_conclusion(result)
    try:
        if should_post(result) and result is not None:
            if not reviewed_sha_is_current(args.repo, args.pr, args.sha):
                print("pull request head moved; not publishing this run", file=sys.stderr)
                return 1
            publish_review(args.repo, args.pr, args.sha, result)
            conclusion, title, summary = check_conclusion(result)
        elif (
            result is not None
            and (result.get("status") not in (None, "", "ok") or not result.get("targets"))
        ):
            notice = failure_notice(result, str(args.review_rc))
            print(notice, file=sys.stderr)
            _post_review(
                args.repo,
                args.pr,
                {"commit_id": args.sha, "body": notice, "event": "COMMENT"},
            )
        if args.check_id:
            complete_check(args.repo, int(args.check_id), conclusion, title, summary)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{CHECK_NAME} {conclusion}: {title}")
    if title.startswith("NEEDS_CHANGES"):
        print(f"::warning title=NEEDS_CHANGES::{summary or title}")
    if conclusion != "success" and summary:
        print(summary, file=sys.stderr)
    return 0 if conclusion == "success" else 1


def _cmd_conclude(args: argparse.Namespace) -> int:
    """Exit 0 when the review ran. Does not post a review."""
    result = result_for_publish(args.result, args.review_rc)
    conclusion, title, summary = check_conclusion(result)
    print(f"{CHECK_NAME} {conclusion}: {title}")
    if title.startswith("NEEDS_CHANGES"):
        print(f"::warning title=NEEDS_CHANGES::{summary or title}")
    if conclusion != "success" and summary:
        print(summary, file=sys.stderr)
    return 0 if conclusion == "success" else 1


def _cmd_gate_sha(args: argparse.Namespace) -> int:
    try:
        print(
            resolve_gate_sha(
                args.repo,
                args.head,
                args.merge,
                ignore_run_id=args.ignore_run_id,
            )
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="plaibook review GitHub check")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve = sub.add_parser("resolve")
    resolve.add_argument("--event-name", default="")
    resolve.add_argument("--event-path", default="")
    resolve.add_argument("--repo", default="")
    resolve.set_defaults(func=_cmd_resolve)

    gate_cmd = sub.add_parser("gate")
    gate_cmd.add_argument("--repo", required=True)
    gate_cmd.add_argument("--sha", required=True)
    gate_cmd.add_argument("--merge", default="")
    gate_cmd.add_argument("--ignore-run-id", default="")
    gate_cmd.set_defaults(func=_cmd_gate)

    gate_sha_cmd = sub.add_parser("gate-sha")
    gate_sha_cmd.add_argument("--repo", required=True)
    gate_sha_cmd.add_argument("--head", required=True)
    gate_sha_cmd.add_argument("--merge", default="")
    gate_sha_cmd.add_argument("--ignore-run-id", default="")
    gate_sha_cmd.set_defaults(func=_cmd_gate_sha)

    open_cmd = sub.add_parser("open-check")
    open_cmd.add_argument("--repo", required=True)
    open_cmd.add_argument("--sha", required=True)
    open_cmd.add_argument("--pr", required=True)
    open_cmd.set_defaults(func=_cmd_open)

    fail_cmd = sub.add_parser("fail-check")
    fail_cmd.add_argument("--repo", required=True)
    fail_cmd.add_argument("--sha", required=True)
    fail_cmd.add_argument("--pr", required=True)
    fail_cmd.add_argument("--check-id", default="")
    fail_cmd.add_argument("--title", required=True)
    fail_cmd.add_argument("--summary", required=True)
    fail_cmd.set_defaults(func=_cmd_fail)

    publish = sub.add_parser("publish")
    publish.add_argument("--repo", required=True)
    publish.add_argument("--sha", required=True)
    publish.add_argument("--pr", required=True)
    publish.add_argument("--check-id", default="")
    publish.add_argument("--result", default="")
    publish.add_argument("--review-rc", default="0")
    publish.set_defaults(func=_cmd_publish)

    conclude = sub.add_parser("conclude")
    conclude.add_argument("--result", default="")
    conclude.add_argument("--review-rc", default="0")
    conclude.set_defaults(func=_cmd_conclude)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
