# -*- coding: utf-8 -*-
"""Read last_run.<run_id>.json (and optional summary.json). Do not rescore."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, TextIO

import yaml

SEVERITY_ORDER = ("critical", "major", "minor", "nit")
_ANSI_RE = re.compile(
    r"(?:\x1b[@-Z\\-_]"
    r"|\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[PX^_].*?(?:\x1b\\|\x07))"
)
# C0 except TAB/LF (those stay in multi-line report text), DEL, and 8-bit C1
# (U+0080–U+009F). U+009B CSI is the non-ESC CSI that _ANSI_RE does not match.
_C0_C1_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class SummaryError(Exception):
    """last_run or summary JSON could not be read or parsed."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SummaryError(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SummaryError(f"cannot decode {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SummaryError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SummaryError(f"{path} must be a JSON object, not {type(loaded).__name__}")
    return loaded


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
                "guardian_scan",
            ):
                if key in summary and key not in entry:
                    entry[key] = summary[key]
                elif key in summary and key in ("scores", "findings_count", "findings", "guardian_scan"):
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
    """Human review: target, verdict, 0-100 scores, and every non-refuted finding.

    Full findings.md is not dumped unless ``full`` (``--full`` / ``-v``).
    SKIPPED (CI preflight) always prints the reason and failing check
    names; the 0.0 score is omitted. NOTHING_TO_REVIEW (an empty
    commit range) prints that reason and no score, so it cannot look
    like ``status: ok``. A guardian-forced NEEDS_CHANGES is
    named on the default TTY so 100% plus that verdict is not silent.
    """
    lines: list[str] = []
    targets = document.get("targets") or []
    if not targets:
        status = sanitize_display_line(document.get("status") or "unknown")
        error = sanitize_display_line(document.get("error") or "")
        lines.append(f"status: {status}")
        if error:
            lines.append(error)
        lines.extend(_footer(document, targets))
        return "\n".join(lines) + "\n"

    for target in targets:
        verdict = sanitize_display_line(target.get("verdict") or document.get("status") or "UNKNOWN")
        score = target.get("score_overall", target.get("score"))
        skipped = _is_skipped(target, verdict)
        empty_range = _is_empty_range(target, verdict)
        score_text = "" if skipped or empty_range else _percent(score)
        name = sanitize_display_line(target.get("target") or "")
        header = f"{verdict}"
        if score_text:
            header = f"{verdict}  {score_text}"
        if name:
            header = f"{header}  {name}"
        lines.append(header)
        if document.get("exploration_incomplete"):
            lines.append("  exploration incomplete (a search did not finish; that is not 'no matches')")

        guardian_line = _guardian_block_line(document, target)
        if guardian_line:
            lines.append(guardian_line)

        cache_line = _cache_hit_line(document, target)
        if cache_line:
            lines.append(cache_line)

        if skipped:
            lines.extend(_skipped_lines(target))
        elif empty_range:
            reason = str(target.get("skip_reason") or target.get("report") or "").strip()
            if reason:
                lines.append(f"  {sanitize_display_line(reason)}")

        scores = target.get("scores") or {}
        if scores:
            lens_bits = []
            for lens in ("functionality", "security", "quality"):
                if lens in scores:
                    lens_bits.append(f"{lens} {_percent(scores[lens])}")
            extra = [
                f"{sanitize_display_line(str(k))} {_percent(v)}"
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
            bits = [
                f"{sanitize_display_line(counts.get(sev, 0))} {sev}" for sev in SEVERITY_ORDER
            ]
            lines.append("  findings: " + ", ".join(bits))

        for finding in findings:
            if str(finding.get("evidence_status") or "").lower() == "refuted":
                continue
            lines.extend(_format_point_finding(finding))

        if full:
            report = sanitize_display_text((target.get("report") or "").strip())
            if report:
                lines.append("")
                lines.append(report)

    lines.extend(_footer(document, targets))
    return "\n".join(lines) + "\n"


def _is_skipped(target: dict[str, Any], verdict: str) -> bool:
    return verdict.upper() == "SKIPPED" or bool(target.get("ci_preflight_failed"))


def _is_empty_range(target: dict[str, Any], verdict: str) -> bool:
    """A commit range with nothing between the endpoints. Not a scored review."""
    return verdict.upper() == "NOTHING_TO_REVIEW" or bool(target.get("range_empty"))


def _skipped_lines(target: dict[str, Any]) -> list[str]:
    """Why a review was SKIPPED, on the default TTY (not only -v / --full)."""
    lines: list[str] = []
    reason = str(target.get("skip_reason") or "").strip()
    if not reason:
        reason = _skip_reason_from_report(str(target.get("report") or ""))
    if reason:
        lines.append(f"  {sanitize_display_line(reason)}")

    check_names = _failing_check_names(target)
    if check_names:
        lines.append("  Failing checks:")
        for name in check_names:
            lines.append(f"    - {name}")

    hint = str(target.get("skip_hint") or "").strip()
    if not hint and (
        target.get("ci_preflight_failed")
        or "review_require_ci_passing" in str(target.get("report") or "")
    ):
        hint = "Pass `-e review_require_ci_passing=false` to bypass."
    if hint and hint not in reason and "review_require_ci_passing" not in reason:
        lines.append(f"  {sanitize_display_line(hint)}")
    return lines


def _failing_check_names(target: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for check in target.get("failing_checks") or []:
        if isinstance(check, dict):
            raw = check.get("name") or ""
        else:
            raw = check
        name = sanitize_display_line(raw)
        if name:
            names.append(name)
    if names:
        return names
    return _failing_check_names_from_report(str(target.get("report") or ""))


def _skip_reason_from_report(report: str) -> str:
    for line in sanitize_display_text(report).splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("review skipped:"):
            return sanitize_display_line(stripped)
    return ""


_FAILING_CHECK_MD_RE = re.compile(r"^-\s+\*\*(.+?)\*\*")


def _failing_check_names_from_report(report: str) -> list[str]:
    names: list[str] = []
    for line in (report or "").splitlines():
        match = _FAILING_CHECK_MD_RE.match(line.strip())
        if match:
            name = sanitize_display_line(match.group(1))
            if name:
                names.append(name)
    return names


def _guardian_block_line(document: dict[str, Any], target: dict[str, Any]) -> str | None:
    """Name an ai-guardian verdict override on the default TTY, not only in findings.md."""
    scan = target.get("guardian_scan") if isinstance(target.get("guardian_scan"), dict) else {}
    forced = bool(
        document.get("guardian_forced_needs_changes")
        or target.get("guardian_forced_needs_changes")
        or scan.get("forced_needs_changes")
    )
    if not forced:
        return None
    raw_ids = (
        document.get("guardian_blocking_rule_ids")
        or target.get("guardian_blocking_rule_ids")
        or scan.get("blocking_rule_ids")
        or []
    )
    ids = [sanitize_display_line(item) for item in raw_ids if item]
    label = ", ".join(ids) if ids else "SECRET-001"
    return (
        f"  blocked by ai-guardian ({label}): independent of lens scores; "
        "see findings.md Security Scan"
    )


def _cache_hit_line(document: dict[str, Any], target: dict[str, Any]) -> str | None:
    if not target.get("cache_hit"):
        return None
    sha = sanitize_display_line(str(target.get("commit") or document.get("commit") or "").strip())
    if sha:
        return (
            f"  same-commit cache hit for {sha} "
            "(no new agents; $0.00 is expected). Re-run with -f to force."
        )
    return "  same-commit cache hit (no new agents; $0.00 is expected). Re-run with -f to force."


def _strip_format_controls(text: str) -> str:
    """Drop Unicode Cf (bidi overrides, zero-width, BOM) after C0/C1 removal."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def sanitize_display_text(text: str) -> str:
    """Strip ANSI/OSC, C0/C1, and bidi/zero-width from untrusted review text."""
    cleaned = _ANSI_RE.sub("", text or "")
    return _strip_format_controls(_C0_C1_RE.sub("", cleaned))


def sanitize_display_line(text: Any) -> str:
    """Sanitize a single output field: no ANSI/C0/C1/bidi, and no forged extra lines."""
    cleaned = _ANSI_RE.sub("", str(text if text is not None else ""))
    cleaned = cleaned.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return " ".join(_strip_format_controls(_C0_C1_RE.sub("", cleaned)).split())


def _format_point_finding(finding: dict[str, Any]) -> list[str]:
    sev = sanitize_display_line(str(finding.get("severity") or "Finding").capitalize())
    path = finding.get("file") or finding.get("path") or "?"
    line = finding.get("line")
    loc = f"{path}:{line}" if line not in (None, "") else str(path)
    loc = sanitize_display_line(loc)
    title, why = _finding_title_why(finding)
    title = sanitize_display_line(title)
    why = sanitize_display_line(why)
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
            lines.append(f"  cost: {sanitize_display_line(cost)}")
    path = document.get("last_run_path")
    if path:
        lines.append(f"  last_run: {sanitize_display_line(path)}")
    for target in targets or []:
        findings_md = (
            target.get("findings_run_scoped_path") or target.get("findings_path") or ""
        )
        if findings_md:
            lines.append(f"  findings.md: {sanitize_display_line(findings_md)}")
    return lines


def _percent(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return sanitize_display_line(value)


def _count_findings(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for finding in findings:
        sev = str(finding.get("severity") or "").lower()
        if sev in counts:
            counts[sev] += 1
    return counts
