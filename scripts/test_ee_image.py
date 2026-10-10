# -*- coding: utf-8 -*-
"""The execution environment builds from a clean checkout and publishes to GHCR."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
EE = ROOT / "execution-environment.yml"
README = ROOT / "README.md"
PR = ROOT / ".github" / "workflows" / "ee.yml"
PUBLISH = ROOT / ".github" / "workflows" / "ee-publish.yml"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
BASE = (
    "registry.access.redhat.com/hi/python:3.12-builder"
    "@sha256:aa64794afb234eac11f21e2ca7c8430753f268a1a755d16f7f9c88ca5975bc33"
)


def _load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in document and "on" not in document:
        document["on"] = document.pop(True)
    return document


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start + len(heading) :]
    next_heading = rest.find("\n## ")
    return rest if next_heading < 0 else rest[:next_heading]


def test_execution_environment_builds_from_the_pin_file():
    text = EE.read_text(encoding="utf-8")
    header, _, _ = text.partition("\nversion:")
    document = _load(EE)
    assert "bug_pipeline.yml" not in header
    assert "ansible-plaibook-ee" not in text
    assert "ghcr.io/aknochow/plaibook-ee" in header
    assert "ssh_proxy.py" not in text.split("additional_build_steps:", 1)[-1]
    assert document["additional_build_files"] == [
        {"src": "execution-environment-gh-key.asc", "dest": "keys"}
    ]
    assert "BEGIN PGP PUBLIC KEY BLOCK" in (ROOT / "execution-environment-gh-key.asc").read_text(encoding="utf-8")
    assert "rpm --import /tmp/gh-key.asc && rpm -i /tmp/gh.rpm" in text
    assert "sha256sum -c - && rpm -i --nosignature /tmp/glab.rpm" in text
    assert document["images"]["base_image"]["name"] == BASE
    assert document["options"]["package_manager_path"] == "/usr/bin/dnf"
    assert document["options"]["user"] == "1000"
    assert document["dependencies"]["python_interpreter"]["python_path"] == "/usr/bin/python3"
    assert document["dependencies"]["galaxy"] == "collections-requirements.yml"
    assert document["dependencies"]["python"] == "execution-environment-requirements.txt"
    assert document["dependencies"]["ansible_core"]["package_pip"] == "--require-hashes -r /tmp/ee-requirements.txt"
    assert "ansible_runner" not in document["dependencies"]
    locked = (ROOT / "execution-environment-requirements.txt").read_text(encoding="utf-8")
    assert ">=" not in locked
    for pin in (
        "ansible-core==2.19.14",
        "ansible-runner==2.4.3",
        "anthropic[vertex]==1.13.0",
        "claude-agent-sdk==0.2.165",
        "google-genai==2.29.0",
        "openai==3.28.0",
        "cursor-sdk==1.0.37",
        "openshell==0.1.3",
    ):
        assert pin in locked
    assert "--hash=sha256:" in locked
    assert "gh-cli.repo" not in text
    assert "eba164e2b9be93d210690010436ff4fb7834221dd902b4fbac37a61d0124520e" in text
    assert "51d3e63d4c323c336ddfb710dad6e1ed40a960baa950bcbe5e21b2e7475d7388" in text
    assert "sha256sum -c -" in text


def test_readme_points_at_the_published_image():
    text = README.read_text(encoding="utf-8")
    section = _section(text, "## Running it in AAP")
    assert "publishes a prebuilt image" not in text
    assert "follow-up PR" not in text
    assert "both sibling collections" not in text
    assert "ghcr.io/aknochow/plaibook-ee" in section
    assert "linux/amd64 and linux/arm64" in section
    assert "collections-requirements.yml" in section
    assert "version tag for AAP Job Templates" in section
    assert ":main" in section and ":latest" in section
    assert "ssh_proxy.py" in section
    assert "cosign verify" in section
    assert "ansible-builder build -t <your-registry>/plaibook-ee:latest" in section
    assert "build/collections/*.tar.gz" not in section


def test_pr_workflow_builds_both_arches_and_does_not_push():
    text = PR.read_text(encoding="utf-8")
    document = _load(PR)
    assert set(document["on"]) == {"pull_request"}
    assert document["permissions"] == {}
    job = document["jobs"]["build"]
    assert job["permissions"] == {"contents": "read"}
    arches = {row["arch"]: row["runner"] for row in job["strategy"]["matrix"]["include"]}
    assert arches == {"amd64": "ubuntu-latest", "arm64": "ubuntu-24.04-arm"}
    assert "packages:" not in text
    assert "id-token:" not in text
    assert "podman push" not in text
    assert "secrets." not in text
    assert text.count(CHECKOUT) == 1
    assert "persist-credentials: false" in text
    assert "ignore-unfixed: \"true\"" in text or 'ignore-unfixed: "true"' in text
    assert "severity: HIGH,CRITICAL" in text
    assert "--require-hashes -r .github/ee-builder-requirements.txt" in text
    assert document["on"]["pull_request"]["paths"].count("scripts/ee_smoke.py") == 1
    assert ".trivyignore.yaml" in document["on"]["pull_request"]["paths"]
    _assert_trivy_gate(job)
    ee = EE.read_text(encoding="utf-8")
    assert "sha256sum -c -" in ee
    assert "8ab5addd123aa50ffb4ca5cbfe16c62bc4ab5dc4f146e3e7f9fd086f41778f8d" in ee
    assert "951891c88b76bb8ff3e1fedea4b84015edc498eb44c5f8a636752bda9f48cfe8" in ee


def test_publish_workflow_signs_only_on_push_and_release():
    text = PUBLISH.read_text(encoding="utf-8")
    document = _load(PUBLISH)
    assert "pull_request" not in document["on"]
    assert document["on"]["release"]["types"] == ["published"]
    assert "main" in document["on"]["push"]["branches"]
    assert document["permissions"] == {}
    build = document["jobs"]["build"]
    manifest = document["jobs"]["manifest"]
    smoke = document["jobs"]["smoke"]
    assert build["permissions"]["packages"] == "write"
    assert build["permissions"]["id-token"] == "write"
    assert manifest["permissions"]["packages"] == "write"
    assert manifest["permissions"]["id-token"] == "write"
    assert smoke["permissions"] == {"contents": "read", "packages": "read"}
    assert "id-token" not in smoke["permissions"]
    assert "cosign sign" in text
    assert "cosign attest" in text
    assert 'syft "docker-archive:${RUNNER_TEMP}/plaibook-ee.tar"' in text
    assert "podman:plaibook-ee:smoke" not in text
    assert "cosign verify" in text
    issuer = re.search(r"--certificate-oidc-issuer (\S+)", text)
    assert issuer is not None
    parsed = urlparse(issuer.group(1))
    assert parsed.scheme == "https"
    assert parsed.hostname == "token.actions.githubusercontent.com"
    assert "ee-publish.yml@${GITHUB_REF}" in text
    assert "--require-hashes -r .github/ee-builder-requirements.txt" in text
    assert ".trivyignore.yaml" in document["on"]["push"]["paths"]
    login = 'printf \'%s\' "$GHCR_TOKEN" | podman login --username "$GHCR_USER" --password-stdin ghcr.io'
    cosign_login = (
        'printf \'%s\' "$GHCR_TOKEN" | cosign login ghcr.io '
        '--username "$GHCR_USER" --password-stdin'
    )
    assert text.count(login) == 3
    assert text.count(cosign_login) == 2
    assert '-p "$GHCR_TOKEN"' not in text
    _assert_trivy_gate(build)
    assert text.count("persist-credentials: false") == 3
    assert CHECKOUT in text
    arches = {row["arch"] for row in build["strategy"]["matrix"]["include"]}
    assert arches == {"amd64", "arm64"}


def _assert_trivy_gate(job: dict) -> None:
    steps = {step["name"]: step for step in job["steps"]}
    gate = steps["Fail on fixable high and critical vulnerabilities"]["with"]
    report = steps["Report vulnerabilities"]["with"]
    assert gate["severity"] == "HIGH,CRITICAL"
    assert gate["exit-code"] == "1"
    assert gate["ignore-unfixed"] == "true"
    assert gate["trivyignores"] == ".trivyignore.yaml"
    assert "trivyignores" not in report


def test_trivyignore_covers_the_unfixed_vendor_highs():
    document = _load(ROOT / ".trivyignore.yaml")
    findings = document["vulnerabilities"]
    ids = {item["id"] for item in findings}
    assert ids == {
        "CVE-2026-19481",
        "CVE-2026-12151",
        "CVE-2026-1526",
        "CVE-2026-2229",
        "GHSA-6v7p-g79w-8964",
        "CVE-2025-47273",
        "CVE-2026-97687",
        "CVE-2026-97689",
        "CVE-2026-78669",
        "CVE-2026-78667",
        "CVE-2026-97031",
    }
    for item in findings:
        assert item["statement"] == "upstream-vendored, no fix we can apply"
        assert str(item["expired_at"]) == "2026-11-09"
        assert item["purls"]


def test_smoke_subprocess_timeout_fails_closed():
    smoke = _load_script("ee_smoke")
    assert smoke.COMMAND_TIMEOUT == 120
    assert smoke.PODMAN_TIMEOUT == 300
    try:
        smoke._run(["sleep", "30"], timeout=1)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("a timed-out smoke command must exit")


def test_manifest_check_requires_both_arches():
    arches = _load_script("ee_manifest_arches")
    platforms = arches.platforms
    missing_platforms = arches.missing_platforms
    document = {
        "manifests": [
            {"platform": {"os": "linux", "architecture": "amd64"}},
            {"platform": {"os": "linux", "architecture": "arm64"}},
        ]
    }
    assert platforms(document) == {("linux", "amd64"), ("linux", "arm64")}
    assert missing_platforms(document) == []
    assert missing_platforms({"manifests": [{"platform": {"os": "linux", "architecture": "amd64"}}]}) == [
        ("linux", "arm64")
    ]


def test_git_pins_reads_collection_requirements():
    pins = _load_script("ee_smoke").git_pins((ROOT / "collections-requirements.yml").read_text(encoding="utf-8"))
    urls = [url for url, _sha in pins]
    assert "https://github.com/aknochow/ansible-cursor.git" in urls
    assert "https://github.com/aknochow/ansible-gemini.git" in urls
    assert all(len(sha) >= 7 for _url, sha in pins)
    assert len(pins) >= 8


def test_manifest_cli_accepts_an_index():
    arches = _load_script("ee_manifest_arches")
    sample = {
        "manifests": [
            {"platform": {"os": "linux", "architecture": "amd64"}},
            {"platform": {"os": "linux", "architecture": "arm64"}},
        ]
    }
    assert arches.missing_platforms(json.loads(json.dumps(sample))) == []
