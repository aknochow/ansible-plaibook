# -*- coding: utf-8 -*-
"""Read last_run.<run_id>.json (and optional summary.json). Do not rescore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

import yaml

SEVERITY_ORDER = ("critical", "major", "minor", "nit")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary_file_for_target(target: dict[str, Any]) -> Path | None:
    """Prefer the collision-immune sibling persist.yml also writes.

    ``summary_path`` is last-write-wins for a given target/commit.
    ``summary_run_scoped_path`` is this invocation's own file. Concurrent
    reviews of the same SHA can overwrite the canonical path, so a CLI
    that enriches from it can print another run's scores with no error.

    If the run-scoped path is advertised but the file is missing, do not
    fall through to the canonical path — last_run already carries this
    run's verdict/score from persist, and using summary.json would mix
    in another run.
    """
    run_scoped = target.get("summary_run_scoped_path") or ""
    if run_scoped:
        path = Path(run_scoped)
        return path if path.is_file() else None
    canonical = target.get("summary_path") or ""
    if canonical:
        path = Path(canonical)
        if path.is_file():
            return path
    return None


def enrich_last_run(last_run: dict[str, Any], *, last_run_file: Path) -> dict[str, Any]:
    """Pass-through last_run, attaching summary.json fields the playbook already wrote."""
    document = dict(last_run)
    document["last_run_path"] = str(last_run_file)
    targets_out = []
    for target in last_run.get("targets") or []:
        entry = dict(target)
        path = _summary_file_for_target(target)
        if path is not None:
            summary = load_json(path)
            for key in (
                "scores",
                "score_overall",
                "findings_count",
                "findings",
                "commit",
                "branch",
                "date",
            ):
                if key in summary and key not in entry:
                    entry[key] = summary[key]
                elif key in summary and key in ("scores", "findings_count", "findings"):
                    entry[key] = summary[key]
            if "score" not in entry and "score_overall" in summary:
                entry["score"] = summary["score_overall"]
            if "verdict" not in entry and "verdict" in summary:
                entry["verdict"] = summary["verdict"]
        targets_out.append(entry)
    document["targets"] = targets_out
    return document


def dump_json(document: dict[str, Any], stream: TextIO) -> None:
    json.dump(document, stream, indent=2, sort_keys=True)
    stream.write("\n")


def dump_yaml(document: dict[str, Any], stream: TextIO) -> None:
    yaml.safe_dump(document, stream, sort_keys=True, default_flow_style=False)


def format_pretty(document: dict[str, Any], *, full: bool = False) -> str:
    """Human review: target, verdict, 0-100 scores, Critical/Major bodies.

    Minor/nit stay as counts. Full findings.md is not dumped unless
    ``full`` (``--full`` / ``-v``).
    """
    lines: list[str] = []
    targets = document.get("targets") or []
    if not targets:
        status = document.get("status") or "unknown"
        error = document.get("error") or ""
        lines.append(f"status: {status}")
        if error:
            lines.append(error)
        lines.extend(_footer(document, targets))
        return "\n".join(lines) + "\n"

    for target in targets:
        verdict = target.get("verdict") or document.get("status") or "UNKNOWN"
        score = target.get("score_overall", target.get("score"))
        score_text = _percent(score)
        name = target.get("target") or ""
        header = f"{verdict}"
        if score_text:
            header = f"{verdict}  {score_text}"
        if name:
            header = f"{header}  {name}"
        lines.append(header)

        cache_line = _cache_hit_line(document, target)
        if cache_line:
            lines.append(cache_line)

        scores = target.get("scores") or {}
        if scores:
            lens_bits = []
            for lens in ("functionality", "security", "quality"):
                if lens in scores:
                    lens_bits.append(f"{lens} {_percent(scores[lens])}")
            extra = [
                f"{k} {_percent(v)}"
                for k, v in scores.items()
                if k not in ("functionality", "security", "quality")
            ]
            if lens_bits or extra:
                lines.append("  " + "  ".join(lens_bits + extra))

        findings = list(target.get("findings") or [])
        counts = target.get("findings_count")
        if not counts and findings:
            counts = _count_findings(findings)
        if counts:
            bits = [f"{counts.get(sev, 0)} {sev}" for sev in SEVERITY_ORDER]
            lines.append("  findings: " + ", ".join(bits))

        for finding in findings:
            if str(finding.get("severity") or "").lower() not in ("critical", "major"):
                continue
            if str(finding.get("evidence_status") or "").lower() == "refuted":
                continue
            lines.extend(_format_point_finding(finding))

        if full:
            report = (target.get("report") or "").strip()
            if report:
                lines.append("")
                lines.append(report)

    lines.extend(_footer(document, targets))
    return "\n".join(lines) + "\n"


def _cache_hit_line(document: dict[str, Any], target: dict[str, Any]) -> str | None:
    if not target.get("cache_hit"):
        return None
    sha = str(target.get("commit") or document.get("commit") or "").strip()
    if sha:
        return (
            f"  same-commit cache hit for {sha} "
            "(no new agents; $0.00 is expected). Re-run with -f to force."
        )
    return "  same-commit cache hit (no new agents; $0.00 is expected). Re-run with -f to force."


def _format_point_finding(finding: dict[str, Any]) -> list[str]:
    sev = str(finding.get("severity") or "Finding").capitalize()
    path = finding.get("file") or finding.get("path") or "?"
    line = finding.get("line")
    loc = f"{path}:{line}" if line not in (None, "") else str(path)
    title, why = _finding_title_why(finding)
    out = [f"  {sev}  {loc}  {title}"]
    if why:
        out.append(f"    {why}")
    return out


def _finding_title_why(finding: dict[str, Any]) -> tuple[str, str]:
    title = str(finding.get("title") or "").strip()
    description = str(finding.get("why") or finding.get("description") or "").strip()
    if title:
        why = description if description != title else ""
        return title, _short_why(why)
    if not description:
        return "untitled finding", ""
    sentence, _, rest = description.partition(". ")
    if rest:
        return _short_why(sentence, limit=120), _short_why(rest)
    return _short_why(description, limit=120), ""


def _short_why(text: str, limit: int = 200) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _footer(document: dict[str, Any], targets: list[dict[str, Any]] | None = None) -> list[str]:
    lines: list[str] = []
    cost = document.get("cost_usd")
    if cost is not None and cost != "":
        try:
            lines.append(f"  cost: ${float(cost):.4f}")
        except (TypeError, ValueError):
            lines.append(f"  cost: {cost}")
    path = document.get("last_run_path")
    if path:
        lines.append(f"  last_run: {path}")
    for target in targets or []:
        findings_md = (
            target.get("findings_run_scoped_path") or target.get("findings_path") or ""
        )
        if findings_md:
            lines.append(f"  findings.md: {findings_md}")
    return lines


def _percent(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _count_findings(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for finding in findings:
        sev = str(finding.get("severity") or "").lower()
        if sev in counts:
            counts[sev] += 1
    return counts
