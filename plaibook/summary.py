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
    """
    for key in ("summary_run_scoped_path", "summary_path"):
        raw = target.get(key) or ""
        if not raw:
            continue
        path = Path(raw)
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


def format_pretty(document: dict[str, Any]) -> str:
    lines: list[str] = []
    targets = document.get("targets") or []
    if not targets:
        status = document.get("status") or "unknown"
        error = document.get("error") or ""
        lines.append(f"status: {status}")
        if error:
            lines.append(error)
        lines.extend(_footer(document))
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

        counts = target.get("findings_count")
        if not counts and target.get("findings"):
            counts = _count_findings(target["findings"])
        if counts:
            bits = [f"{counts.get(sev, 0)} {sev}" for sev in SEVERITY_ORDER]
            lines.append("  findings: " + ", ".join(bits))

    lines.extend(_footer(document))
    return "\n".join(lines) + "\n"


def _footer(document: dict[str, Any]) -> list[str]:
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
