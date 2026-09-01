# -*- coding: utf-8 -*-
"""Behavioral-equivalence tests for filter_self_refuted_findings.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

Two kinds of coverage:
1. Unit tests on the extracted pure functions against the seven real-world
   self-refutation wordings documented in merge.yml's own comments (used
   here as literal fixtures, copied verbatim -- not paraphrased).
2. A byte-for-byte equivalence check: the SAME fixtures are run through
   the PRE-PORT Jinja expression merge.yml used to contain (frozen below
   as LEGACY_JINJA_DROP_EXPRESSION, extracted verbatim via `git show
   HEAD:roles/code_review/tasks/merge.yml` at port time -- merge.yml's
   live task now calls this action plugin instead, so there is no longer
   a live Jinja expression to read out of the file), rendered via
   Ansible's own real Templar, and the two outputs are asserted
   identical.

   Deliberately uses ansible.template.Templar rather than a bare
   jinja2.Environment: Templar.do_template() runs the source through
   Templar._escape_backslashes() before compiling it (escape_backslashes
   defaults to True) specifically so a single backslash written in a
   playbook -- '\\b', '\\s', '\\w' -- survives Jinja's own Python-style
   string-literal decoding intact, rather than being decoded as a Python
   escape (e.g. '\\b' silently becoming a literal backspace byte). A bare
   jinja2.Environment does NOT do this preprocessing, so it would
   misrepresent what actually happened when Ansible ran the old
   merge.yml for real -- confirmed the hard way while writing this test,
   when a bare NativeEnvironment falsely appeared to show three of the
   seven documented wordings going uncaught in "production."
"""
from __future__ import annotations

import pytest
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

try:
    from ansible.template import trust_as_template
except ImportError:
    def trust_as_template(data: str) -> str:
        return data

from filter_self_refuted_findings import (
    FIX_REFUTED_PATTERN,
    ActionModule,
    filter_self_refuted_findings,
    is_self_refuted,
)

# Frozen verbatim from `git show HEAD:roles/code_review/tasks/merge.yml`
# (the "Drop self-refuted findings..." task's combined_findings value) at
# the moment this action plugin replaced it. This is deliberately NOT read
# live from merge.yml -- once the port lands, merge.yml no longer contains
# this expression at all, so there is nothing live left to read.
LEGACY_JINJA_DROP_EXPRESSION = (
    "{{ combined_findings\n"
    "   | rejectattr('evidence', 'search', '(?i)refut')\n"
    "   | rejectattr('fix', 'search', '(?i)refut|dropping\\b|drop(?:ped|ping)? (this|the) finding|'\n"
    "       ~ '^\\s*(n/?a\\b|not applicable)|no\\s+(?:\\w+\\s+){0,2}(?:fix|change|action)\\w*\\s*"
    "(?:is\\s+|are\\s+|was\\s+)?(?:needed|required)|\\b(?:is|are)\\s+"
    "(?:actually\\s+|already\\s+)?correct\\b')\n"
    "   | rejectattr('description', 'search', '(?i)no actual (bug|issue|vulnerability|problem)\\b')\n"
    "   | list }}"
)


def _render_legacy_jinja_expression(findings):
    """Evaluate the pre-port rejectattr chain via Ansible's real Templar."""
    templar = Templar(loader=DataLoader())
    templar.available_variables = {"combined_findings": findings}
    # trust_as_template(): ansible-core 2.21+ added a template-trust check
    # that Templar.template() enforces by silently returning the input
    # STRING UNCHANGED (not raising) when it isn't marked trusted -- a raw
    # Python string literal built in test code is untrusted by default.
    # Confirmed live: without this wrapper, this helper returned
    # LEGACY_JINJA_DROP_EXPRESSION itself (unrendered) instead of the
    # filtered findings list, failing every equivalence assertion below
    # against a broken baseline -- not a real semantic mismatch between
    # this plugin and merge.yml's actual behavior. Real Ansible playbook
    # content (loaded from a .yml file via DataLoader) is trusted
    # automatically; only this kind of in-process literal needs tagging.
    return templar.template(trust_as_template(LEGACY_JINJA_DROP_EXPRESSION))


# The seven wordings are copied verbatim from merge.yml's comments (lines
# 28-65 as of this writing) documenting each real occurrence that defeated
# the previous, narrower regex.
SELF_REFUTED_FIXTURES = [
    pytest.param(
        {"evidence": "e", "fix": "N/A - refuted", "description": "d"},
        id="na-dash-refuted",
    ),
    pytest.param(
        {"evidence": "e", "fix": "N/A — self-refuted, dropping materiality", "description": "d"},
        id="na-emdash-self-refuted-dropping-materiality",
    ),
    pytest.param(
        {"evidence": "e", "fix": "N/A — behavior is correct.", "description": "d"},
        id="na-emdash-behavior-is-correct",
    ),
    pytest.param(
        {"evidence": "e", "fix": "No fix needed — the refactor is correct.", "description": "d"},
        id="no-fix-needed-refactor-correct",
    ),
    pytest.param(
        {"evidence": "e", "fix": "No security fix needed — dropping this finding.", "description": "d"},
        id="no-security-fix-needed-dropping-this-finding",
    ),
    pytest.param(
        {"evidence": "e", "fix": "Logic is correct upon re-examination — dropping.", "description": "d"},
        id="logic-correct-bare-trailing-dropping",
    ),
    pytest.param(
        {
            "evidence": "e",
            "fix": "Consider adding followlinks=False to the tarfile.add() call.",
            "description": (
                "This is the documented behavior and is correct. "
                "No actual bug here after re-examination."
            ),
        },
        id="self-retraction-lives-only-in-description",
    ),
]

# Wordings caught live AFTER the port -- the frozen LEGACY_JINJA_DROP_
# EXPRESSION above never had a chance to catch these (it's a snapshot
# of the pre-port Jinja, which by definition predates them), so these
# are checked against a LIVE render of this module's own current
# pattern constants instead (see test_live_pattern_constants_match_
# python_re_evaluation below), not the frozen legacy expression.
POST_PORT_SELF_REFUTED_FIXTURES = [
    pytest.param(
        {
            "evidence": "e",
            "fix": (
                "Not actionable enough to warrant a change — this is an "
                "observation about timezone inconsistency in the footer "
                "display, not a functional defect."
            ),
            "description": "d",
        },
        id="not-actionable-enough-to-warrant-a-change",
    ),
]


def _render_live_pattern_search(field, pattern, findings):
    """Evaluate `findings | rejectattr(field, 'search', pattern)` via a real Templar.

    Mirrors merge.yml's own pre-port rejectattr chain shape, but against
    this module's CURRENT pattern constants (which _render_legacy_jinja_
    expression's frozen string does not track) -- catches any future
    divergence between how Python's re.search and Jinja's 'search' test
    interpret the same pattern string (the exact class of footgun that
    made a bare jinja2.NativeEnvironment misrepresent three of the
    original seven wordings as uncaught, see module docstring).
    """
    templar = Templar(loader=DataLoader())
    templar.available_variables = {"combined_findings": findings, "pattern": pattern, "field": field}
    # trust_as_template() -- see _render_legacy_jinja_expression's own
    # comment above for why this is required on ansible-core 2.21+.
    survivors = templar.template(trust_as_template("{{ combined_findings | rejectattr(field, 'search', pattern) | list }}"))
    return len(survivors) < len(findings)  # True if `pattern` matched and dropped the finding


# Findings that must NOT be dropped -- legitimate, actionable findings that
# happen to brush up against the regexes' vocabulary (e.g. mention
# "correct" describing the FIX's outcome, not the existing code).
LEGITIMATE_FIXTURES = [
    pytest.param(
        {
            "evidence": "tar.extractall() called without a filter argument",
            "fix": "Add filter='data' to the extractall() call to prevent path traversal.",
            "description": "Archive extraction can write outside the target directory.",
        },
        id="real-path-traversal-finding",
    ),
    pytest.param(
        {
            "evidence": "response body is interpolated directly into the SQL string",
            "fix": "Use a parameterized query instead of string formatting.",
            "description": "This makes the query correct under all inputs once parameterized.",
        },
        id="fix-field-mentions-correct-as-outcome-not-existing-code",
    ),
]


@pytest.mark.parametrize("finding", SELF_REFUTED_FIXTURES + POST_PORT_SELF_REFUTED_FIXTURES)
def test_self_refuted_wording_is_detected(finding):
    assert is_self_refuted(finding) is True


@pytest.mark.parametrize("finding", LEGITIMATE_FIXTURES)
def test_legitimate_finding_is_not_dropped(finding):
    assert is_self_refuted(finding) is False


@pytest.mark.parametrize("finding", POST_PORT_SELF_REFUTED_FIXTURES)
def test_post_port_wording_matches_live_jinja_search_of_current_pattern(finding):
    """The current FIX_REFUTED_PATTERN constant, rendered live through
    Ansible's real Templar, must agree with is_self_refuted's Python
    re.search evaluation -- the equivalence check for wordings added
    after the port, since the frozen legacy snapshot predates them."""
    assert _render_live_pattern_search("fix", FIX_REFUTED_PATTERN, [finding]) is True
    assert is_self_refuted(finding) is True


@pytest.mark.parametrize("finding", LEGITIMATE_FIXTURES)
def test_legitimate_finding_also_survives_live_jinja_search_of_current_pattern(finding):
    assert _render_live_pattern_search("fix", FIX_REFUTED_PATTERN, [finding]) is False


def test_filter_drops_only_self_refuted_findings():
    self_refuted = [param.values[0] for param in SELF_REFUTED_FIXTURES + POST_PORT_SELF_REFUTED_FIXTURES]
    legitimate = [param.values[0] for param in LEGITIMATE_FIXTURES]
    survivors = filter_self_refuted_findings(self_refuted + legitimate)
    assert survivors == legitimate


@pytest.mark.parametrize("finding", SELF_REFUTED_FIXTURES + LEGITIMATE_FIXTURES)
def test_python_port_matches_legacy_jinja_expression(finding):
    """The actual behavioral-equivalence check: same input, same verdict."""
    python_survivors = filter_self_refuted_findings([finding])
    jinja_survivors = _render_legacy_jinja_expression([finding])
    assert python_survivors == jinja_survivors


def test_python_port_matches_legacy_jinja_expression_as_a_batch():
    """Equivalence check across the full mixed batch in one pass, not just
    singleton lists -- guards against any order-dependence divergence."""
    all_findings = [param.values[0] for param in SELF_REFUTED_FIXTURES] + [
        param.values[0] for param in LEGITIMATE_FIXTURES
    ]
    python_survivors = filter_self_refuted_findings(all_findings)
    jinja_survivors = _render_legacy_jinja_expression(all_findings)
    assert python_survivors == jinja_survivors


# --- ActionModule wiring smoke test ------------------------------------
#
# Deliberately minimal, hand-rolled test doubles rather than a general
# ActionBase test harness -- properly solving "how do you unit-test an
# ActionBase subclass in isolation" is its own separate handoff item
# (research-action-plugin-test-harness-pattern) and is out of scope here.
# This only proves the run() wiring (arg extraction, result shape) matches
# the pure function it delegates to.


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "filter_self_refuted_findings"
        self.async_val = False
        self.check_mode = False


def _run_action_module(findings):
    action = ActionModule(
        task=_FakeTask({"findings": findings}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    self_refuted = [param.values[0] for param in SELF_REFUTED_FIXTURES + POST_PORT_SELF_REFUTED_FIXTURES]
    findings = self_refuted + [param.values[0] for param in LEGITIMATE_FIXTURES]
    result = _run_action_module(findings)
    assert "failed" not in result
    assert result["findings"] == filter_self_refuted_findings(findings)
    assert result["dropped_count"] == len(self_refuted)


def test_action_module_requires_findings_arg():
    action = ActionModule(
        task=_FakeTask({}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
