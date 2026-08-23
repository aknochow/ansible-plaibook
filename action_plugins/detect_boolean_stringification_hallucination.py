# -*- coding: utf-8 -*-
"""Shared action plugin: recognize the recurring, empirically-false
"Jinja/Ansible stringifies booleans" claim shape on sight.

handoff.ansible-plaibook-review-false-positives.yaml's
ansible-jinja-boolean-stringification-hallucination item documents 8
occurrences of a finding claiming that a boolean value produced by this
pipeline's own Jinja/Ansible templating (a bare `{{ expr }}` comparison
or lookup assigned via set_fact, or even a bare YAML boolean literal)
gets coerced into the Python string "True"/"False" instead of staying
a native bool -- and that this breaks downstream truthiness checks
(`if that_value:`, `selectattr('field')`) into always-true, regardless
of the real value. Empirically false every single time it was checked,
escalating rigor each time (see that item's own description) --
matches this codebase's own documented root cause
(merge_verify_result.py's docstring): the one real stringification
gotcha in this codebase's non-native Jinja templating is dot-accessed
INTEGERS only, never booleans.

handoff.ansible-plaibook-verify-stage-regression-boolean-stringification.yaml
escalated this from "catalog of false positives" to "fix it": a 9th
occurrence (MR !42, gitlab-aknochow-ansible-plaibook-42#49ddf29) got past
verify.yml's own independent LLM re-check (review_verify_model
defaults to claude-haiku-4-5) and shipped as a real Critical finding --
the first confirmed case of that re-check getting this specific,
well-characterized pattern wrong after a clean 8-occurrence track
record. Investigated before building this: verify.yml verifies each
finding in its own fresh, independent conversation (verify_finding.yml
resets verify_messages per finding), ruling out cross-finding
contamination within the same run as a mechanism; no raw transcript of
a prior correctly-refuted occurrence survived on disk to diff against
byte-for-byte (review_debug_dump_prompts wasn't enabled for that live
MR run); re-confirmed the underlying claim false a third time via a
fresh, independent empirical reproduction. No structural pipeline bug
was found -- the LLM-reliability gap itself, on a claim shape this
well-characterized, is the thing worth routing around deterministically
rather than re-litigating from scratch every time.

Matches this codebase's established pattern for a recurring,
well-evidenced LLM failure mode (check_evidence_provenance.py,
compute_suggested_severity.py, coerce_findings_encoding.py,
filter_self_refuted_findings.py's regex-based classification): a small
deterministic Python check, not another prompt-level admonition --
prompt-level instructions already exist in the lens prompts and
clearly aren't sufficient alone, given 9 real occurrences including one
that got past the LLM re-check meant to catch exactly this.

Scoped narrowly to the actual recurring claim shape via three
independently-required signals (all three must match, on the finding's
combined evidence+description+fix text) -- NOT a general hallucination
detector:
  1. A stringification claim: the text asserts a boolean becomes a
     string (mentions "string" near "true"/"false", or says the value
     gets converted/rendered/stored/serialized/cast "as"/"to"/"into" a
     string).
  2. A Jinja/Ansible-specific origin: the claim is about THIS
     pipeline's own templating mechanism (mentions Jinja/set_fact, or
     the evidence quotes a `{{ ... }}` expression or a bare YAML
     `key: true/false` literal) -- distinguishes this from a genuinely
     different bug where a value really is a string for an unrelated
     reason (e.g. a REST API returning a literal JSON string "true").
  3. A truthiness-breaking downstream consequence: the claim says this
     makes something always-true/always-correct/always-selected
     regardless of the real value, or invokes Python's own
     non-empty-string-is-truthy rule.

A finding matching all three is deterministically short-circuited to
evidence_status: refuted in verify_finding.yml, bypassing the LLM
verify turn entirely for that finding -- cheaper AND more reliable than
asking a fresh model to re-derive the same correct conclusion every
time.
"""
from __future__ import annotations

import re

from ansible.plugins.action import ActionBase

# Signal 1: a claim that a value becomes a STRING version of a boolean.
# Dogfooding finding (this repo's own review of this exact MR): the
# original bridge used a negated-dot class ([^.]{0,80}) meant to avoid
# crossing sentence boundaries, but that also rejects a legitimate
# wording with an embedded dot within the window (e.g. "the string,
# e.g. 'False'..."), risking a false negative. Non-greedy any-char
# bridging (.{0,80}?) still keeps the match tight (shortest span first)
# without an accidental dot-exclusion side effect. Deliberately NOT
# re.DOTALL: '.' still shouldn't cross a newline, since combined puts
# evidence/description/fix on separate lines -- widening the bridge to
# span those field boundaries would be a different, unrequested change
# (see combined's own construction below for the same limitation noted
# where it's actually relevant). Also fixed: `stored?` only matched
# "store"/"stored", missing the third-person "stores" -- `stores?`
# plus the trailing \w* below still covers "stored" too.
_STRINGIFICATION_PATTERN = re.compile(
    r"(?i)\bstring\b.{0,80}?\b(?:['\"]?true['\"]?|['\"]?false['\"]?)\b"
    r"|\b(?:['\"]?true['\"]?|['\"]?false['\"]?)\b.{0,80}?\bstring\b"
    r"|stringif"
    r"|(?:convert|render|serializ|cast|returns?|yields?|comes?\s+back|stores?)\w*\s+"
    r"(?:as\s+|to\s+|into\s+)(?:the\s+|a\s+)?(?:python\s+)?string"
)

# Signal 2: the claim is about THIS pipeline's own Jinja/Ansible
# templating, not some unrelated string-vs-bool bug.
_JINJA_ORIGIN_PATTERN = re.compile(r"(?i)jinja2?|set_fact|\{\{|ansible.*templat")

# Signal 3: a truthiness-breaking downstream consequence.
_TRUTHINESS_CONSEQUENCE_PATTERN = re.compile(
    r"(?i)truthy"
    r"|non-empty string"
    r"|always[\s-]+(?:true|correct|selected|counted|truthy)"
    r"|regardless of (?:the )?(?:actual )?(?:result|value|correctness)"
    r"|selectattr"
)


def is_boolean_stringification_hallucination(finding: dict) -> bool:
    """Return True if `finding` matches the recurring, empirically-false
    'Jinja/Ansible stringifies booleans' claim shape (see module docstring)."""
    evidence = finding.get("evidence") or ""
    description = finding.get("description") or ""
    fix = finding.get("fix") or ""
    # \n-joined, not space-joined: _STRINGIFICATION_PATTERN's bridge is
    # deliberately '.' (never crosses a newline), so a claim split across
    # fields -- "string" in evidence, "true"/"false" only in description
    # -- won't match on that branch alone. Matches this module's own
    # regex comment above; noted here too since this is where a future
    # reader changing the join separator would actually feel the effect.
    combined = f"{evidence}\n{description}\n{fix}"
    return bool(
        _STRINGIFICATION_PATTERN.search(combined)
        and _JINJA_ORIGIN_PATTERN.search(combined)
        and _TRUTHINESS_CONSEQUENCE_PATTERN.search(combined)
    )


class ActionModule(ActionBase):
    """Detect the recurring boolean-stringification hallucination claim shape."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("finding",))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        finding = self._task.args.get("finding")
        if finding is None:
            result["failed"] = True
            result["msg"] = "detect_boolean_stringification_hallucination requires a 'finding' argument"
            return result

        result["changed"] = False
        result["is_hallucination"] = is_boolean_stringification_hallucination(finding)
        return result
