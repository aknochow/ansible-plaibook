# -*- coding: utf-8 -*-
"""The execution environment builds from a clean checkout and publishes to GHCR."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
    assert "additional_build_files" not in document
    assert document["images"]["base_image"]["name"] == BASE
    assert document["options"]["package_manager_path"] == "/usr/bin/dnf"
    assert document["options"]["user"] == "1000"
    assert document["dependencies"]["python_interpreter"]["python_path"] == "/usr/bin/python3"
    assert document["dependencies"]["galaxy"] == "collections-requirements.yml"
    python_deps = document["dependencies"]["python"]
    for pin in (
        "anthropic[vertex]>=0.84.0",
        "claude-agent-sdk>=0.2.144",
        "google-genai>=1.0.0",
        "openai>=1.58.0",
        "cursor-sdk>=1.0.31,<2.0.0",
        "openshell>=0.0.116,<0.0.120",
    ):
        assert pin in python_deps


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
    assert "cosign verify" in text
    assert "https://token.actions.githubusercontent.com" in text
    assert "ee-publish.yml@${GITHUB_REF}" in text
    assert text.count("persist-credentials: false") == 3
    assert CHECKOUT in text
    arches = {row["arch"] for row in build["strategy"]["matrix"]["include"]}
    assert arches == {"amd64", "arm64"}


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
