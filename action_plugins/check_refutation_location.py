# -*- coding: utf-8 -*-
"""Keep a finding when its refutation quotes a sibling block of the cited code.

A file can hold two similar blocks, for example two dict mappings that
share most of their keys. The verifier can then read the wrong one,
quote it, and refute a finding about the other. That happened on a true
Minor: the finding cited the run-ledger mapping, which omitted a key,
and the refutation quoted the last-run mapping 130 lines earlier, which
had it.

This check reads the cited file and finds where each quoted line of the
refutation's evidence occurs. It turns a ``refuted`` verdict into
``inconclusive`` only when both hold:

- every quoted line that occurs once in the file is more than
  ``window`` lines from the cited line, and
- some other quoted line occurs more than once, with one occurrence
  within ``window`` lines of the cited line and another within
  ``window`` lines of those unique lines (the far copy of the block).

That is the shape of a quote taken from a copy of the cited code. A
refutation that quotes a definition elsewhere in the file (no line
shared with the cited block), another file, or a paraphrase is left
alone: nothing in the cited file matches, so there is nothing to judge.
An inconclusive finding stays in the review and in the score.

Blocks are judged by line distance, not parsed. A repeated line that
happens to sit near an unrelated quoted line can still match. The
error only ever keeps a finding the verifier refuted, never drops one.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

# Shorter fragments (a bare `}` or `return x`) occur all over a file and
# would make any two blocks look related.
_MIN_FRAGMENT = 12
_BACKTICK_SPAN = re.compile(r"`([^`\n]+)`")


def _fragments(text: str) -> list[str]:
    """Candidate code fragments quoted in an evidence string."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        for piece in [raw, *_BACKTICK_SPAN.findall(raw)]:
            frag = piece.strip().strip('`"').strip()
            if len(frag) >= _MIN_FRAGMENT and frag not in out:
                out.append(frag)
    return out


def _locations(fragment: str, lines: list[str]) -> list[int]:
    """1-based line numbers whose stripped text contains the fragment."""
    return [n for n, line in enumerate(lines, start=1) if fragment in line.strip()]


def check_refutation_location(
    status: str,
    cited_line: int,
    verification_evidence: str,
    file_content: str,
    window: int = 10,
) -> dict:
    """Return the status to keep, and a note when the verdict was changed."""
    unchanged = {"status": status, "downgraded": False, "note": ""}
    if status != "refuted" or not file_content or cited_line < 1:
        return unchanged

    lines = file_content.splitlines()
    located = {f: locs for f in _fragments(verification_evidence) if (locs := _locations(f, lines))}
    if not located:
        return unchanged

    def near(n: int) -> bool:
        return abs(n - cited_line) <= window

    unique = [locs[0] for locs in located.values() if len(locs) == 1]
    if not unique or any(near(n) for n in unique):
        return unchanged
    # The repeated line must sit both at the cited block and in the far
    # block the unique lines came from; otherwise the quote mixes the
    # cited code with an unrelated line elsewhere, which is a fair read.
    def in_far_block(n: int) -> bool:
        return not near(n) and any(abs(n - u) <= window for u in unique)

    sibling = any(
        any(near(n) for n in locs) and any(in_far_block(n) for n in locs)
        for locs in located.values()
        if len(locs) > 1
    )
    if not sibling:
        return unchanged

    first, last = min(unique), max(unique)
    span = f"line {first}" if first == last else f"lines {first}-{last}"
    note = (
        f"The refutation quoted {span} of this file, a similar block away from "
        f"the cited line {cited_line}, so it may describe the other block. "
        "Kept as inconclusive."
    )
    return {"status": "inconclusive", "downgraded": True, "note": note}


class ActionModule(ActionBase):
    """Downgrade a refutation that quotes a sibling block of the cited code."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("status", "cited_line", "verification_evidence", "file_content", "window"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        args = self._task.args
        try:
            cited_line = int(args.get("cited_line") or 0)
            window = int(args.get("window", 10))
        except (TypeError, ValueError):
            cited_line, window = 0, 10

        result.update(
            check_refutation_location(
                status=str(args.get("status") or ""),
                cited_line=cited_line,
                verification_evidence=str(args.get("verification_evidence") or ""),
                file_content=str(args.get("file_content") or ""),
                window=window,
            )
        )
        result["changed"] = False
        return result
