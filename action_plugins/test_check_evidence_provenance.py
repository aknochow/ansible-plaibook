# -*- coding: utf-8 -*-
"""Behavioral tests for check_evidence_provenance.py.

Run with the ansible+jinja2-equipped interpreter, e.g.:
    /opt/homebrew/bin/python3.10 -m pytest action_plugins/

The fixture below is a real reproduction case: a prior round's findings.md
(REAL_PRIOR_REVIEW_MD) got injected as context into the next round, and
some of that next round's findings (REAL_ROUND_2_FINDINGS) turned out to
be echoes of the prior content rather than fresh findings against the
actual current diff (REAL_CURRENT_DIFF).

Of the 7 findings in REAL_ROUND_2_FINDINGS, 4 are genuine echoes this
plugin catches (evidence matching the prior review but absent from the
current diff, including one case only caught after comment-leader
normalization strips '#' markers the echo dropped). The other 3 are
verified NOT echoes: one is a real match dropped mid-quote (outside what
comment-leader normalization compensates for), one is genuinely fresh
text the current diff actually adds, and one is a hallucination
fabricated from a filename mention rather than copied finding prose,
a structurally different problem this mechanism isn't designed to catch.
"""
from __future__ import annotations

from check_evidence_provenance import ActionModule, check_evidence_provenance

# A prior round's persisted findings.md, injected into the next round
# as review_prior_review_md.
REAL_PRIOR_REVIEW_MD = r"""---
date: 2026-08-18
commit: 7f3e864
project: "aknochow/ansible-plaibook"
branch: "gitlab-aknochow-ansible-plaibook-25"
verdict: READY_FOR_HUMAN_REVIEW
score: 90.0
scores:
  functionality: 85.0
  security: 90.0
  quality: 95.0
---
## Code Review: aknochow/ansible-plaibook (gitlab-aknochow-ansible-plaibook-25)

### Verdict: READY_FOR_HUMAN_REVIEW

### Scores
| Lens | Score | Findings |
|---|---|---|
| Functionality | 85.0% | 3 |
| Security | 90.0% | 2 |
| Quality | 95.0% | 1 |
| **Overall** | **90.0%** | |

### Findings

#### Critical
None.

#### Major
None.

#### Minor
1. **[Security]** **examples/live_verification.yml:47** Internal gateway URLs and GCP project IDs are hardcoded in a file being added to the repository.
   - **Evidence**: `rdu:
        gateway: "https://og.apps.ocp.example.com"
        bearer_token: "{{ lookup('env', 'OPENSHELL_BEARER_TOKEN_RDU') }}"
      sno:
        gateway: "https://ogo-e2e-abc1234.apps.sno.example.com"
        bearer_token: "{{ lookup('env', 'OPENSHELL_BEARER_TOKEN_SNO') }}"

    ...
    claude_vertex_project: example-gcp-project
    gemini_vertex_project: example-gcp-project`
   - **Confidence**: MEDIUM
   - **Points**: 5
2. **[Security]** **docs/sandbox-and-agent-safety.md:52** The documented SSH ProxyCommand pattern uses `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`.
   - **Evidence**: `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ...`
   - **Confidence**: LOW
   - **Points**: 5
3. **[Functionality]** **examples/_live_verification_target.yml:20** Default port fallback of 80 is wrong for a plaintext gRPC gateway on 127.0.0.1:8081.
   - **Evidence**: `port: "{{ sandbox_gateway | urlsplit('port') | default(80, true) }}"`
   - **Confidence**: LOW
   - **Points**: 5
4. **[Functionality]** **examples/_live_verification_target.yml:38** The comment on lines 38-42 says 'rescue/always do not fire on UNREACHABLE (only on ordinary task FAILED)'.
   - **Evidence**: `# ignore_unreachable + an explicit fail check right after, NOT a bare
    # rescue/always -- rescue/always do not fire on UNREACHABLE (only on
    # ordinary task FAILED), only on ordinary FAILED, so an SSH connection
    # problem here would otherwise skip teardown and leak the sandbox.`
   - **Confidence**: MEDIUM
   - **Points**: 5
5. **[Quality]** **examples/live_verification.yml:56** The `ssh_proxy_script` path is hardcoded to `$HOME/code/ansible-openshell/scripts/ssh_proxy.py`.
   - **Evidence**: `ssh_proxy_script: "{{ lookup('env', 'HOME') }}/code/ansible-openshell/scripts/ssh_proxy.py"`
   - **Confidence**: HIGH
   - **Points**: 5
6. **[Functionality]** **examples/_live_verification_target.yml:43** delegate_to uses review_delegate_host without the `| default(omit, true)` guard.
   - **Evidence**: `Line 43: `delegate_to: "{{ review_delegate_host }}"` vs. the pattern in roles/review/tasks/briefing.yml line 146: `delegate_to: "{{ review_delegate_host | default(omit, true) }}"``
   - **Confidence**: MEDIUM
   - **Points**: 5

#### Nit
None.
"""

# The current round's diff. Touches exactly one file.
REAL_CURRENT_DIFF = r"""diff --git a/examples/_live_verification_target.yml b/examples/_live_verification_target.yml
index f52190e..e6de302 100644
--- a/examples/_live_verification_target.yml
+++ b/examples/_live_verification_target.yml
@@ -34,13 +34,14 @@
     - name: "[{{ target_name }}] Set up sandbox"
       ansible.builtin.include_tasks: ../tasks/setup_sandbox.yml

-    # ignore_unreachable + an explicit fail check right after, NOT a bare
-    # rescue/always -- rescue/always do not fire on UNREACHABLE (only on
-    # ordinary task FAILED), only on ordinary FAILED, so an SSH connection
-    # problem here would otherwise skip teardown and leak the sandbox.
-    # See docs/sandbox-and-agent-safety.md.
+    # The block's own `always:` below does NOT fire on its own if this
+    # task goes UNREACHABLE (a broken SSH ProxyCommand, e.g.) -- only on
+    # an ordinary task FAILED. ignore_unreachable here, plus the explicit
+    # fail task right after, converts an UNREACHABLE into an ordinary
+    # FAILED so the existing `always:` teardown actually runs instead of
+    # leaking the sandbox. See docs/sandbox-and-agent-safety.md.
     - name: "[{{ target_name }}] Run an isolated command inside the sandbox"
-      delegate_to: "{{ review_delegate_host }}"
+      delegate_to: "{{ review_delegate_host | default(omit, true) }}"
       ignore_unreachable: true
       ansible.builtin.command: uname -a
       register: sandbox_uname
"""

# The next round's findings, having received REAL_PRIOR_REVIEW_MD as
# injected prior-round context.
REAL_ROUND_2_FINDINGS = [
    {
        "lens": "Security",
        "file": "examples/live_verification.yml",
        "line": 47,
        "severity": "Minor",
        "description": "hardcoded gateway URLs",
        "evidence": (
            'rdu:\n        gateway: "https://og.apps.ocp.example.com"\n'
            "        bearer_token: \"{{ lookup('env', 'OPENSHELL_BEARER_TOKEN_RDU') }}\"\n"
            '      sno:\n        gateway: "https://ogo-e2e-abc1234.apps.sno.example.com"\n'
            "        bearer_token: \"{{ lookup('env', 'OPENSHELL_BEARER_TOKEN_SNO') }}\"\n\n"
            "    claude_vertex_project: example-gcp-project\n"
            "    gemini_vertex_project: example-gcp-project"
        ),
        "confidence": "MEDIUM",
        "fix": "move to env lookups",
    },
    {
        "lens": "Security",
        "file": "docs/sandbox-and-agent-safety.md",
        "line": 52,
        "severity": "Minor",
        "description": "documented SSH pattern disables host key checking",
        "evidence": "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ...",
        "confidence": "MEDIUM",
        "fix": "add a caveat",
    },
    {
        "lens": "Security",
        "file": "examples/openshell_workspace_member.yml",
        "line": 28,
        "severity": "Minor",
        "description": "documented shell command with unquoted expansion",
        "evidence": (
            "SA_TOKEN=$(oc create token ansible-plaibook-live-verify -n ansible-plaibook)\n"
            "#   curl -s -X POST https://<auth-bridge-route>/token/exchange \\\n"
            '#     -H "Authorization: Bearer $SA_TOKEN"'
        ),
        "confidence": "MEDIUM",
        "fix": "quote the variable",
    },
    {
        "lens": "Functionality",
        "file": "examples/_live_verification_target.yml",
        "line": 20,
        "severity": "Minor",
        "description": "default port fallback of 80 is wrong for gRPC",
        "evidence": "port: \"{{ sandbox_gateway | urlsplit('port') | default(80, true) }}\"",
        "confidence": "HIGH",
        "fix": "change the default",
    },
    {
        "lens": "Functionality",
        "file": "examples/live_verification.yml",
        "line": 56,
        "severity": "Minor",
        "description": "hardcoded developer-specific path",
        "evidence": "ssh_proxy_script: \"{{ lookup('env', 'HOME') }}/code/ansible-openshell/scripts/ssh_proxy.py\"",
        "confidence": "HIGH",
        "fix": "parameterize it",
    },
    {
        "lens": "Quality",
        "file": "docs/sandbox-and-agent-safety.md",
        "line": 42,
        "severity": "Minor",
        "description": "duplicate phrase in comment",
        "evidence": (
            "rescue/always do not fire on UNREACHABLE (only on\nordinary task FAILED), "
            "only on ordinary FAILED, so an SSH connection\nproblem here would otherwise "
            "skip teardown and leak the sandbox."
        ),
        "confidence": "MEDIUM",
        "fix": "reword",
    },
    {
        "lens": "Quality",
        "file": "examples/_live_verification_target.yml",
        "line": 38,
        "severity": "Minor",
        "description": "comment could be clearer",
        "evidence": (
            "# The block's own `always:` below does NOT fire on its own if this\n"
            "    # task goes UNREACHABLE (a broken SSH ProxyCommand, e.g.) -- only on\n"
            "    # an ordinary task FAILED. ignore_unreachable here, plus the explicit\n"
            "    # fail task right after, converts an UNREACHABLE into an ordinary\n"
            "    # FAILED so the existing `always:` teardown actually runs instead of\n"
            "    # leaking the sandbox. See docs/sandbox-and-agent-safety.md."
        ),
        "confidence": "MEDIUM",
        "fix": "N/A, this is the fix itself",
    },
]

CAUGHT_LINES = {
    ("docs/sandbox-and-agent-safety.md", 52),
    ("examples/live_verification.yml", 56),
    ("examples/_live_verification_target.yml", 20),
    ("docs/sandbox-and-agent-safety.md", 42),  # only via comment-leader normalization
}

NOT_CAUGHT_LINES = {
    ("examples/live_verification.yml", 47),  # echo dropped a '...' elision marker
    ("examples/openshell_workspace_member.yml", 28),  # not a prior-round echo at all
}

# Genuinely new text the diff itself adds. Must not be flagged.
GENUINELY_FRESH_LINE = ("examples/_live_verification_target.yml", 38)


def test_real_mr25_reproduction_catches_verified_cases_and_only_those():
    result = check_evidence_provenance(REAL_ROUND_2_FINDINGS, REAL_PRIOR_REVIEW_MD, REAL_CURRENT_DIFF)
    by_key = {(f["file"], f["line"]): f for f in result}

    for key in CAUGHT_LINES:
        finding = by_key[key]
        assert finding["prior_context_echoed"] is True, key
        assert finding["evidence_status"] == "refuted", key
        assert finding["verification_evidence"] == finding["evidence"]
        assert "check_evidence_provenance" in finding["verification_rationale"]

    for key in NOT_CAUGHT_LINES | {GENUINELY_FRESH_LINE}:
        finding = by_key[key]
        assert finding["prior_context_echoed"] is False, key
        assert finding["evidence_status"] is None, key


def test_real_mr25_reproduction_catches_exactly_four_of_seven():
    result = check_evidence_provenance(REAL_ROUND_2_FINDINGS, REAL_PRIOR_REVIEW_MD, REAL_CURRENT_DIFF)
    flagged = [f for f in result if f["prior_context_echoed"]]
    assert len(flagged) == len(CAUGHT_LINES) == 4
    assert len(result) == 7


# --- Synthetic edge cases -----------------------------------------------


def test_persisting_real_issue_not_flagged_when_evidence_still_in_current_diff():
    # A real, unfixed issue reported again where the evidence text is
    # naturally present in both the prior review and the current diff
    # must survive untouched.
    evidence = "def get_config_value(raw_config, key):\n    return raw_config[key].strip()"
    prior_md = f"1. **[Functionality]** app/config.py:2 some finding.\n   - **Evidence**: `{evidence}`"
    current_diff = f"--- a/app/config.py\n+++ b/app/config.py\n@@ -1,2 +1,2 @@\n {evidence}\n+# unrelated added line"
    findings = [{"file": "app/config.py", "line": 2, "evidence": evidence, "severity": "Minor", "lens": "Functionality"}]

    result = check_evidence_provenance(findings, prior_md, current_diff)
    assert result[0]["prior_context_echoed"] is False
    assert result[0]["evidence_status"] is None


def test_empty_prior_review_md_never_flags_anything():
    findings = [
        {"file": "a.py", "line": 1, "evidence": "x" * 30, "severity": "Minor", "lens": "Security"},
    ]
    result = check_evidence_provenance(findings, "", "some diff content")
    assert result[0]["prior_context_echoed"] is False
    assert result[0]["evidence_status"] is None


def test_short_evidence_below_min_length_never_flagged_even_if_it_matches():
    short_evidence = "pass"  # 4 chars, well under the 20-char guard
    prior_md = f"some prior finding citing `{short_evidence}` as evidence"
    findings = [{"file": "a.py", "line": 1, "evidence": short_evidence, "severity": "Minor", "lens": "Quality"}]

    result = check_evidence_provenance(findings, prior_md, "unrelated diff with no overlap")
    assert result[0]["prior_context_echoed"] is False


def test_comment_leader_stripped_before_matching():
    # Prior content quotes a multi-line comment with its '#' continuation
    # markers; the echo drops them and re-presents the same words as
    # flowing prose. Must still match.
    prior_evidence = "# first line of the comment\n    # second line continues the same thought here"
    echoed_evidence = "first line of the comment\nsecond line continues the same thought here"
    prior_md = f"1. Some finding.\n   - **Evidence**: `{prior_evidence}`"
    findings = [{"file": "a.py", "line": 1, "evidence": echoed_evidence, "severity": "Minor", "lens": "Quality"}]

    result = check_evidence_provenance(findings, prior_md, "totally unrelated diff content")
    assert result[0]["prior_context_echoed"] is True


def test_whitespace_variants_still_match():
    # A citation reformatted with different internal indentation must
    # still be recognized as the same text.
    evidence_in_finding = "if\tcondition:\n\t\tdo_the_thing_that_matters_here()"
    evidence_in_prior_prose = "if   condition:\n\t\t\tdo_the_thing_that_matters_here()"
    prior_md = f"1. Some finding.\n   - **Evidence**: `{evidence_in_prior_prose}`"
    findings = [{"file": "a.py", "line": 1, "evidence": evidence_in_finding, "severity": "Minor", "lens": "Quality"}]

    result = check_evidence_provenance(findings, prior_md, "totally unrelated diff content")
    assert result[0]["prior_context_echoed"] is True


def test_none_evidence_normalizes_to_empty_not_the_string_none():
    # A finding with evidence explicitly set to None must not normalize
    # to the 4-character string "None" (str(None) is long enough to slip
    # past the length guard on a coincidental "none" match in prior_review_md).
    prior_md = "some prior review content that happens to mention the word None here"
    findings = [{"file": "a.py", "line": 1, "evidence": None, "severity": "Minor", "lens": "Quality"}]

    result = check_evidence_provenance(findings, prior_md, "unrelated diff")
    assert result[0]["prior_context_echoed"] is False
    assert result[0]["evidence_status"] is None


def test_findings_dict_not_mutated_in_place():
    original = {"file": "a.py", "line": 1, "evidence": "x" * 30, "severity": "Minor", "lens": "Security"}
    findings = [original]
    check_evidence_provenance(findings, "prior md containing " + "x" * 30, "unrelated diff")
    assert "evidence_status" not in original
    assert "prior_context_echoed" not in original


# --- ActionModule wiring smoke test (same hand-rolled test-double pattern
# as dedupe_findings.py's own test file) --------------------------------


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "check_evidence_provenance"
        self.async_val = False
        self.check_mode = False


def _run_action_module(findings, prior_review_md, full_diff):
    action = ActionModule(
        task=_FakeTask({"findings": findings, "prior_review_md": prior_review_md, "full_diff": full_diff}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_action_module_run_matches_pure_function():
    result = _run_action_module(REAL_ROUND_2_FINDINGS, REAL_PRIOR_REVIEW_MD, REAL_CURRENT_DIFF)
    assert "failed" not in result
    assert result["findings"] == check_evidence_provenance(REAL_ROUND_2_FINDINGS, REAL_PRIOR_REVIEW_MD, REAL_CURRENT_DIFF)


def test_action_module_requires_all_three_args():
    action = ActionModule(
        task=_FakeTask({"findings": []}),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    result = action.run(task_vars={})
    assert result["failed"] is True
