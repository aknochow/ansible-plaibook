# -*- coding: utf-8 -*-
"""The public review workflow runs plai and does not call a controller."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".github" / "workflows" / "plai-review-run.yml"
CALLER = ROOT / ".github" / "workflows" / "plai-review.yml"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 parses the bare key "on" as true.
    if True in document and "on" not in document:
        document["on"] = document.pop(True)
    return document


def test_reusable_workflow_permissions_and_pins():
    text = RUN.read_text(encoding="utf-8")
    document = _load(RUN)
    assert document["permissions"] == {}
    assert "workflow_call" in document["on"]
    assert "workflow_run" not in document["on"]
    assert list(document["jobs"]) == ["wait", "review"]
    wait = document["jobs"]["wait"]
    job = document["jobs"]["review"]
    assert wait["name"] == "wait"
    assert job["name"] == "review"
    assert job["needs"] == "wait"
    assert job["if"] == "always() && needs.wait.result != 'cancelled'"
    fail = next(
        step for step in job["steps"] if step.get("name") == "Fail when this run is not a review of passed checks"
    )
    assert "exit 1" in fail["run"]
    assert "needs.wait.outputs.state != 'passed'" in fail["if"]
    assert "Wait until the other checks on this commit have passed" not in [
        step.get("name") for step in job["steps"]
    ]
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "checks": "read",
    }
    assert job["runs-on"] == "ubuntu-26.04-arm"
    assert job["environment"] == {"name": "plaibook-review", "deployment": False}
    assert "environment" not in wait
    assert "strategy" not in job
    assert job["permissions"]["checks"] == "read"
    assert "actions" not in job["permissions"]
    plai_steps = [step for step in job["steps"] if step.get("id") == "plai"]
    assert len(plai_steps) == 1
    assert plai_steps[0]["env"]["OPENAI_API_KEY"] == "${{ secrets.OPENAI_API_KEY }}"
    assert "PLAI_GITHUB_APP_PRIVATE_KEY" not in plai_steps[0].get("env", {})
    assert "USE_SANDBOX" in plai_steps[0]["env"]
    assert plai_steps[0]["env"]["USE_SANDBOX"] == "${{ inputs.use_sandbox }}"
    assert "EVENT_NAME" in plai_steps[0]["env"]
    assert plai_steps[0]["env"]["EVENT_NAME"] == "${{ github.event_name }}"
    assert "sandbox_wait_timeout=600" in plai_steps[0]["run"]
    assert "--no-sandbox" in plai_steps[0]["run"]
    script = (ROOT / "scripts" / "ci_openshell_up.sh").read_text(encoding="utf-8")
    pins = (ROOT / ".github" / "openshell-pins.env").read_text(encoding="utf-8")
    assert "${OPENSHELL_INSTALLER_COMMIT}/install.sh" in script
    assert "sha256sum -c -" in script
    assert "GITHUB_PATH" in script
    assert re.search(r"^OPENSHELL_VERSION=v\d+\.\d+\.\d+$", pins, re.M)
    assert re.search(r"^OPENSHELL_INSTALLER_COMMIT=[0-9a-f]{40}$", pins, re.M)
    assert re.search(r"^OPENSHELL_INSTALLER_SHA256=[0-9a-f]{64}$", pins, re.M)
    image = r"^SANDBOX_IMAGE=ghcr\.io/nvidia/openshell-community/sandboxes/base@sha256:[0-9a-f]{64}$"
    assert re.search(image, pins, re.M)
    assert "openshell-pins.env" in text
    for workflow in (text, (ROOT / ".github" / "workflows" / "openshell-smoke.yml").read_text(encoding="utf-8")):
        load = re.search(r"grep -E '([^']+)' \.github/openshell-pins\.env", workflow)
        assert load
        loaded = [line for line in pins.splitlines() if re.search(load.group(1), line)]
        assert {line.partition("=")[0] for line in loaded} == {
            "OPENSHELL_VERSION",
            "OPENSHELL_INSTALLER_COMMIT",
            "OPENSHELL_INSTALLER_SHA256",
            "SANDBOX_IMAGE",
        }
    assert "base:latest" not in text
    assert 'sha="${GITHUB_SHA}"' not in text
    assert "workflow_run" not in text
    assert "checksums.txt" in text
    assert "plaibook-image-warm" in script
    assert "policy set --global --yes" in script
    assert "OPENSHELL_PROVISION_TIMEOUT" in script
    inputs = document["on"]["workflow_call"]["inputs"]
    assert set(inputs) == {"use_sandbox"}
    assert inputs["use_sandbox"]["type"] == "boolean"
    assert inputs["use_sandbox"]["default"] is True
    checkout = next(step for step in job["steps"] if step.get("name") == "Check out this workflow commit")
    wait_checkout = next(step for step in wait["steps"] if step.get("name") == "Check out this workflow commit")
    assert checkout["with"]["repository"] == "aknochow/ansible-plaibook"
    assert wait_checkout["with"]["repository"] == "aknochow/ansible-plaibook"
    assert checkout["with"]["ref"] == "dea10b445cfa3431c516e0b7c69ec814f570a781"
    assert checkout["with"]["ref"] == wait_checkout["with"]["ref"]
    for step in job["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")
    publish = [step for step in job["steps"] if step.get("name") == "Post the review"]
    assert len(publish) == 1
    assert publish[0]["env"]["PLAI_GITHUB_APP_ID"] == "${{ secrets.PLAI_GITHUB_APP_ID }}"
    assert publish[0]["env"]["PLAI_GITHUB_APP_PRIVATE_KEY"] == "${{ secrets.PLAI_GITHUB_APP_PRIVATE_KEY }}"
    assert "github.token" not in yaml.dump(publish[0])
    secrets = document["on"]["workflow_call"]["secrets"]
    assert set(secrets) == {
        "OPENAI_API_KEY",
        "PLAI_GITHUB_APP_ID",
        "PLAI_GITHUB_APP_PRIVATE_KEY",
    }
    assert all(item["required"] is True for item in secrets.values())
    assert "inherit" not in text
    assert "cursor_api_key" not in text
    assert "github_app_private_key" not in text
    uses = [step["uses"].split()[0] for step in job["steps"] if "uses" in step]
    for item in uses:
        action, _, sha = item.partition("@")
        assert action
        assert _SHA.fullmatch(sha), item
    assert "plai review" in text
    assert "OPENAI_API_KEY" in text
    assert "ANSIBLE_REVIEW_AGENT_FAMILY: openai" in text
    assert "ANSIBLE_REVIEW_OPENAI_MODEL: gpt-6-luna" in text
    assert "PLAIBOOK_PROVIDER_TOKEN" not in text
    assert "checks: write" not in text
    assert "actions: write" not in text
    assert "pull-requests: write" not in text
    assert "review_require_ci_passing=false" in text
    assert "uv sync --locked --no-dev --project" in text
    assert "uv pip install" not in text
    assert '--merge "$MERGE"' in text
    assert "gate-sha" not in text
    assert "job.workflow_sha" not in text
    assert "job.workflow_repository" not in text
    assert "source_sha" not in text
    assert "source_repository" not in text
    assert "GITHUB_WORKFLOW_SHA" not in text
    assert "GITHUB_WORKFLOW_REF" not in text
    assert "github_app_token.py" in text
    assert "PLAI_GITHUB_APP_ID" in text
    assert "::add-mask::" in text
    assert 'echo "$PLAI_GITHUB_APP_PRIVATE_KEY"' not in text
    assert "echo ${PLAI_GITHUB_APP_PRIVATE_KEY}" not in text
    assert "--post" not in text
    lowered = text.lower()
    assert "aap.aknochow.io" not in lowered
    assert "controller_host" not in lowered
    assert "ansible.controller" not in lowered
    assert "plaibook_dispatch_token" not in lowered


def test_caller_does_not_pin_a_floating_secret_ref():
    text = CALLER.read_text(encoding="utf-8")
    document = _load(CALLER)
    assert document["permissions"] == {}
    job = document["jobs"]["review"]
    assert job["name"] == "plai"
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "checks": "read",
    }
    assert job["permissions"]["checks"] == "read"
    assert "environment" not in job
    pinned = (
        "aknochow/ansible-plaibook/.github/workflows/plai-review-run.yml"
        "@8ab8ff99079b43a99e09ccb3bdabdc0ecca393d2"
    )
    assert job["uses"] == pinned
    assert job["with"] == {"use_sandbox": "${{ github.event_name != 'workflow_dispatch' || inputs.use_sandbox }}"}
    assert job["secrets"] == {
        "OPENAI_API_KEY": "${{ secrets.OPENAI_API_KEY }}",
        "PLAI_GITHUB_APP_ID": "${{ secrets.PLAI_GITHUB_APP_ID }}",
        "PLAI_GITHUB_APP_PRIVATE_KEY": "${{ secrets.PLAI_GITHUB_APP_PRIVATE_KEY }}",
    }
    assert "inherit" not in text
    assert "source_sha" not in text
    assert "job.workflow_sha" not in text
    assert "uses: ./." not in text
    assert "@main" not in text
    assert "OPENAI_API_KEY" in text
    assert "PLAIBOOK_PROVIDER_TOKEN" not in text
    assert "PLAIBOOK_DISPATCH_TOKEN" not in text
    assert "checks: write" not in text
    assert "actions: write" not in text
    assert "pull-requests: write" not in text
    assert "author_association == 'COLLABORATOR'" in text
    assert "pull_request" not in document["on"]
    assert document["on"]["pull_request_target"]["types"] == ["opened", "synchronize", "reopened"]
    assert "github.event_name == 'pull_request'" not in text
    assert "github.event_name == 'pull_request_target'" in text
    assert "github.event.repository.default_branch" in text
    assert "head.sha" not in text
    run_text = RUN.read_text(encoding="utf-8")
    assert "head.sha" not in run_text
    assert "github.sha" not in run_text
    assert document["concurrency"]["cancel-in-progress"] is True
    assert document["concurrency"]["group"] == (
        "plaibook-review-${{ github.event.pull_request.number || "
        "github.event.issue.number || github.event.inputs.pr_number || github.run_id }}"
    )
    assert "head.sha" not in document["concurrency"]["group"]


def test_security_event_name_guard_preserves_sandbox_for_pull_request_target_and_issue_comment():
    document = _load(RUN)
    job = document["jobs"]["review"]
    install = next(
        step for step in job["steps"] if step.get("name") == "Start OpenShell and pull the review sandbox"
    )
    assert "github.event_name != 'workflow_dispatch'" in install["if"]
    assert "inputs.use_sandbox" in install["if"]
    plai_step = next(step for step in job["steps"] if step.get("id") == "plai")
    assert plai_step["env"]["USE_SANDBOX"] == "${{ inputs.use_sandbox }}"
    assert plai_step["env"]["EVENT_NAME"] == "${{ github.event_name }}"

    run_script = plai_step["run"]
    assert '[ "${USE_SANDBOX:-true}" = "false" ] && [ "${EVENT_NAME:-}" = "workflow_dispatch" ]' in run_script

    def eval_sandbox_args(use_sandbox: str | None, event_name: str) -> list[str]:
        env_setup = f'EVENT_NAME="{event_name}"\n'
        if use_sandbox is not None:
            env_setup += f'USE_SANDBOX="{use_sandbox}"\n'
        else:
            env_setup += "unset USE_SANDBOX 2>/dev/null || true\n"
        script = f"""
SANDBOX_IMAGE="ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e"
{env_setup}
sandbox_args=(
  -e sandbox_wait_timeout=600
  -e sandbox_image="$SANDBOX_IMAGE"
)
if [ "${{USE_SANDBOX:-true}}" = "false" ] && [ "${{EVENT_NAME:-}}" = "workflow_dispatch" ]; then
  sandbox_args=(--no-sandbox)
fi
printf "%s\\n" "${{sandbox_args[@]}}"
"""
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
        return proc.stdout.splitlines()

    # pull_request_target must retain the sandbox even when use_sandbox is false
    args_pr = eval_sandbox_args("false", "pull_request_target")
    assert "--no-sandbox" not in args_pr
    assert "sandbox_wait_timeout=600" in args_pr
    digest = "sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e"
    assert f"sandbox_image=ghcr.io/nvidia/openshell-community/sandboxes/base@{digest}" in args_pr

    # issue_comment must retain the sandbox even when use_sandbox is false
    args_comment = eval_sandbox_args("false", "issue_comment")
    assert "--no-sandbox" not in args_comment
    assert "sandbox_wait_timeout=600" in args_comment

    # workflow_dispatch with use_sandbox=false passes --no-sandbox
    args_dispatch_nosandbox = eval_sandbox_args("false", "workflow_dispatch")
    assert args_dispatch_nosandbox == ["--no-sandbox"]

    # workflow_dispatch with use_sandbox=true retains the sandbox
    args_dispatch_sandbox = eval_sandbox_args("true", "workflow_dispatch")
    assert "--no-sandbox" not in args_dispatch_sandbox
    assert "sandbox_wait_timeout=600" in args_dispatch_sandbox

    # default / unset USE_SANDBOX retains the sandbox
    args_default = eval_sandbox_args(None, "workflow_dispatch")
    assert "--no-sandbox" not in args_default
    assert "sandbox_wait_timeout=600" in args_default


def test_security_secrets_environment_permissions_and_pins_unchanged():
    document = _load(RUN)
    assert document["permissions"] == {}
    wait = document["jobs"]["wait"]
    job = document["jobs"]["review"]

    expected_perms = {"contents": "read", "pull-requests": "read", "checks": "read"}
    assert wait["permissions"] == expected_perms
    assert job["permissions"] == expected_perms

    assert job["environment"] == {"name": "plaibook-review", "deployment": False}
    assert "environment" not in wait

    secrets = document["on"]["workflow_call"]["secrets"]
    assert set(secrets) == {
        "OPENAI_API_KEY",
        "PLAI_GITHUB_APP_ID",
        "PLAI_GITHUB_APP_PRIVATE_KEY",
    }
    assert all(item["required"] is True for item in secrets.values())

    wait_checkout = next(step for step in wait["steps"] if step.get("name") == "Check out this workflow commit")
    review_checkout = next(step for step in job["steps"] if step.get("name") == "Check out this workflow commit")
    fixed_commit = "dea10b445cfa3431c516e0b7c69ec814f570a781"
    assert wait_checkout["with"]["ref"] == fixed_commit
    assert review_checkout["with"]["ref"] == fixed_commit
    assert wait_checkout["with"]["repository"] == "aknochow/ansible-plaibook"
    assert review_checkout["with"]["repository"] == "aknochow/ansible-plaibook"

    for step in wait["steps"] + job["steps"]:
        if "uses" in step:
            action, _, sha = step["uses"].split()[0].partition("@")
            assert action
            assert _SHA.fullmatch(sha), f"Action not pinned by SHA: {step['uses']}"


def test_security_input_passed_through_env_not_interpolated_in_run_script():
    document = _load(RUN)
    for job_name, job in document["jobs"].items():
        for step in job["steps"]:
            run = step.get("run", "")
            step_name = step.get("name") or step.get("id") or job_name
            assert "${{" not in run, f"Step '{step_name}' interpolates context in run script"
            assert "inputs." not in run, f"Step '{step_name}' references inputs in run script"
            assert "github." not in run, f"Step '{step_name}' references github context in run script"

    plai_step = next(step for step in document["jobs"]["review"]["steps"] if step.get("id") == "plai")
    assert plai_step["env"]["USE_SANDBOX"] == "${{ inputs.use_sandbox }}"
    assert plai_step["env"]["EVENT_NAME"] == "${{ github.event_name }}"


def test_sandbox_enabled_byte_for_byte_equivalent_to_baseline():
    document = _load(RUN)
    plai_step = next(step for step in document["jobs"]["review"]["steps"] if step.get("id") == "plai")
    run_script = plai_step["run"]

    assert "-e review_require_ci_passing=false" in run_script
    assert "-e sandbox_wait_timeout=600" in run_script
    assert '-e sandbox_image="$SANDBOX_IMAGE"' in run_script
