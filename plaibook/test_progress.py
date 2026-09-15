# -*- coding: utf-8 -*-
"""Map ansible task names to the spinner's coarse stages."""

from plaibook.progress import stage_for_task


def test_stage_for_task_main_pipeline():
    assert stage_for_task("Validate the selected provider runtime") == "setup"
    assert stage_for_task("Determine the repo clone URL") == "checkout"
    assert stage_for_task("Set up an OpenShell sandbox for isolated script execution") == "sandbox"
    assert stage_for_task("Scan the shared diff/PR-description content with ai-guardian") == "scan"
    assert stage_for_task("Dispatch the Security and Review lens agents") == "lenses"
    assert stage_for_task("review : Dispatch lenses via provider-native module (agent_family=cursor)") == "lenses"
    assert stage_for_task("Merge findings and compute scores") == "merge"
    assert stage_for_task("Look beyond the diff for additional findings") == "explore"
    assert stage_for_task("Independently verify checkable claims in Critical/Major findings") == "verify"
    assert stage_for_task("Render and persist the findings") == "persist"
    assert stage_for_task("review : Report the same-commit fast path") == "cache"


def test_stage_for_task_ignores_noise():
    assert stage_for_task("Record the review start time") is None
    assert stage_for_task("set_fact") is None
    assert stage_for_task("") is None


def test_format_stage_line_reuses_playbook_clone_url():
    from plaibook.progress import (
        clone_url_from_facts,
        clone_url_from_task_args,
        format_stage_line,
    )

    assert format_stage_line("checkout") == "checkout"
    assert (
        format_stage_line("checkout", clone_url="https://github.com/org/repo.git")
        == "checkout (https://github.com/org/repo.git)"
    )
    assert format_stage_line("lenses", clone_url="https://github.com/org/repo.git") == "lenses"
    assert (
        clone_url_from_facts({"review_clone_url": "https://github.com/aknochow/ansible-plaibook.git"})
        == "https://github.com/aknochow/ansible-plaibook.git"
    )
    assert clone_url_from_facts({"review_clone_url": "{{ review_clone_url }}"}) is None
    assert (
        clone_url_from_task_args({"repo": "https://github.com/aknochow/ansible-plaibook.git"})
        == "https://github.com/aknochow/ansible-plaibook.git"
    )
    token = "https://x-access-token:ghs_secret@github.com/org/repo.git?token=ghs_secret"
    assert "ghs_secret" not in (clone_url_from_facts({"review_clone_url": token}) or "")
    assert clone_url_from_facts({"review_clone_url": token}) == "https://github.com/org/repo.git"
    assert (
        format_stage_line("checkout", clone_url=token)
        == "checkout (https://github.com/org/repo.git)"
    )
    assert "ghs_secret" not in format_stage_line("checkout", clone_url=token)
    assert (
        clone_url_from_task_args({"repo": "git@github.com:org/repo.git"})
        == "git@github.com:org/repo.git"
    )
