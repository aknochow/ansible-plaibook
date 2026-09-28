# -*- coding: utf-8 -*-
"""Build the run-scoped CI artifact a controller job returns to its caller.

The GitHub check reads this document from the job's set_stats artifacts.
It is also written to ci_result.<run_id>.json. It is not last_run.json.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ansible.plugins.action import ActionBase

_FENCE = re.compile(r"^```[^\n`]*\n(.*)\n```$", re.DOTALL)
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./+-][A-Za-z0-9_./+-]*$")
_MAX_ARTIFACT_CHARS = 90000
_MAX_COMMENTS = 30
_PASS_VERDICTS = frozenset({"READY_FOR_HUMAN_REVIEW"})


def concrete_suggestion(finding: dict[str, Any]) -> str | None:
    """Return a drop-in replacement, or None when the fix is only advice.

    ``suggestion`` and ``replacement`` are explicit replacements, including
    multi-line text that GitHub applies in place of the commented line.
    A ``fix`` that is nothing but one fenced block is a suggestion only when
    that block is a single line. Multi-line fenced advice stays a normal comment.
    """
    for key in ("suggestion", "replacement"):
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            text = _unwrap_fence(value.strip())
            if text.strip():
                return text.strip("\n")
    fix = finding.get("fix")
    if not isinstance(fix, str):
        return None
    match = _FENCE.match(fix.strip())
    if not match:
        return None
    inner = match.group(1).strip("\n")
    if not inner or "\n" in inner:
        return None
    return inner


def _unwrap_fence(value: str) -> str:
    match = _FENCE.match(value.strip())
    if match:
        return match.group(1)
    return value


def _line_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number >= 1 else None
    return None


def _safe_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    path = value.strip()
    if not path or path.startswith("/") or ".." in path.split("/"):
        return None
    if not _SAFE_PATH.fullmatch(path):
        return None
    return path


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    text = text.replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def finding_fingerprint(path: str, line: int, severity: str, description: str) -> str:
    raw = f"{path}\n{line}\n{severity}\n{description}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def comment_from_finding(finding: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(finding, dict):
        return None
    if finding.get("evidence_status") == "refuted":
        return None
    path = _safe_path(finding.get("file"))
    line = _line_number(finding.get("line"))
    if path is None or line is None:
        return None
    severity = _clip(finding.get("severity") or "Note", 40)
    lens = _clip(finding.get("lens") or "", 40)
    description = _clip(finding.get("description") or "", 2000)
    evidence = _clip(finding.get("evidence") or "", 500)
    fix = _clip(finding.get("fix") or "", 2000)
    suggestion = concrete_suggestion(finding)
    if suggestion is not None and len(suggestion) > 4000:
        suggestion = None
    fingerprint = finding_fingerprint(path, line, severity, description)
    lines = [f"**{severity}**" + (f" ({lens})" if lens else ""), "", description or "(no description)"]
    if evidence:
        lines.extend(["", "Evidence:", f"`{evidence}`"])
    if suggestion is None and fix:
        lines.extend(["", "Fix:", fix])
    if suggestion is not None:
        lines.extend(["", "```suggestion", suggestion, "```"])
    lines.extend(["", f"<!-- plaibook-finding:{fingerprint} -->"])
    return {
        "path": path,
        "line": line,
        "side": "RIGHT",
        "severity": severity,
        "fingerprint": fingerprint,
        "suggestion": suggestion,
        "body": "\n".join(lines),
    }


def _as_targets(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _target_record(target: dict[str, Any], truncated: list[bool]) -> dict[str, Any]:
    findings = target.get("findings") or []
    if isinstance(findings, str):
        try:
            findings = json.loads(findings)
        except json.JSONDecodeError:
            findings = []
    comments: list[dict[str, Any]] = []
    if isinstance(findings, list):
        for finding in findings:
            if len(comments) >= _MAX_COMMENTS:
                truncated[0] = True
                break
            comment = comment_from_finding(finding)
            if comment is not None:
                comments.append(comment)
    try:
        score = float(target.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "target": _clip(target.get("target") or "", 200),
        "commit": _clip(target.get("commit") or "", 64),
        "verdict": _clip(target.get("verdict") or "", 80),
        "score": score,
        "comments": comments,
    }


def check_conclusion(artifact: dict[str, Any]) -> str:
    """success only when a review actually finished with a passing verdict."""
    if artifact.get("status") != "ok":
        return "failure"
    targets = artifact.get("targets") or []
    if not targets:
        return "failure"
    if all(target.get("verdict") in _PASS_VERDICTS for target in targets):
        return "success"
    return "failure"


def build_ci_review_artifact(
    run_id: str,
    status: str,
    error: str,
    targets: Any,
) -> dict[str, Any]:
    truncated = [False]
    records = [_target_record(target, truncated) for target in _as_targets(targets)]
    artifact = {
        "schema": 1,
        "run_id": str(run_id),
        "status": "failed" if str(status) == "failed" else "ok",
        "error": _clip(error, 2000),
        "last_run_file": f"last_run.{run_id}.json",
        "ci_result_file": f"ci_result.{run_id}.json",
        "truncated": False,
        "targets": records,
    }
    encoded = json.dumps(artifact, sort_keys=True)
    if len(encoded) > _MAX_ARTIFACT_CHARS or truncated[0]:
        for record in records:
            for comment in record["comments"]:
                comment["body"] = _clip(comment["body"], 1500)
                if comment.get("suggestion") and len(comment["suggestion"]) > 500:
                    comment["suggestion"] = None
                    comment["body"] = comment["body"].split("```suggestion", 1)[0].rstrip()
                    comment["body"] += f"\n\n<!-- plaibook-finding:{comment['fingerprint']} -->"
        artifact["truncated"] = True
        artifact["targets"] = records
        encoded = json.dumps(artifact, sort_keys=True)
        if len(encoded) > _MAX_ARTIFACT_CHARS:
            artifact["targets"] = records[:1]
            if artifact["targets"]:
                artifact["targets"][0]["comments"] = artifact["targets"][0]["comments"][:5]
            artifact["truncated"] = True
    artifact["check_conclusion"] = check_conclusion(artifact)
    return artifact


class ActionModule(ActionBase):
    """Serialize verdict, score, and suggestion comments for one controller job."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("run_id", "status", "error", "targets"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = {}
        result = super().run(tmp, task_vars)
        del tmp
        missing = [arg for arg in ("run_id", "status", "targets") if self._task.args.get(arg) is None]
        if missing:
            result["failed"] = True
            result["msg"] = f"build_ci_review_artifact missing: {sorted(missing)}"
            return result
        result["changed"] = False
        result["artifact"] = build_ci_review_artifact(
            run_id=self._task.args.get("run_id"),
            status=self._task.args.get("status"),
            error=self._task.args.get("error") or "",
            targets=self._task.args.get("targets"),
        )
        return result
