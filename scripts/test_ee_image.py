# -*- coding: utf-8 -*-
"""The execution environment builds from a clean checkout and publishes to GHCR."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
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
RESCAN = ROOT / ".github" / "workflows" / "ee-rescan.yml"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
BASE = (
    "registry.access.redhat.com/hi/python:3.12-builder"
    "@sha256:d0c20fb11396ef2486bb80ee94fb82fd0a4c989d51ccb04d65754be5dbd6044b"
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
    assert not (ROOT / "execution-environment-gh-key.asc").exists()
    assert "go1.27.3.linux-${go_arch}.tar.gz" in text
    assert 'echo "${go_sum}  /tmp/go.tar.gz" | sha256sum -c -' in text
    assert "--branch v2.103.3 https://github.com/cli/cli /tmp/gh-src" in text
    assert "= 63837fb778d6a39f02be0ce76ff89f2bc8d74bdc;" in text
    assert "go get golang.org/x/net@v0.62.0;" in text
    assert "tar -C /tmp -xzf" not in text
    assert 'extractall("/tmp", filter="data")' in text
    assert "golang.org/x/net[[:space:]]+v0\\.62\\.0" in text
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
        "anthropic[vertex]==2.6.0",
        "claude-agent-sdk==0.2.179",
        "google-genai==3.0.0",
        "openai==3.46.0",
        "xai-sdk==1.21.0",
    ):
        assert pin in locked
    assert re.search(r"(?m)^openshell==0\.1\.\d+ ", locked)
    assert "cursor-sdk" not in locked
    assert "--hash=sha256:" in locked
    assert "gh-cli.repo" not in text
    assert "gh_2.103.3_linux" not in text
    assert "65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40" in text
    assert "a06e99309dbcbdca1a8ccd2282c3ee146e55930886e26b4f1193269587165f49" in text
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
    assert "ansible-builder build --squash all -t <your-registry>/plaibook-ee:latest" in section
    assert "docs/execution-environment.md" in section
    guide = (ROOT / "docs" / "execution-environment.md").read_text(encoding="utf-8")
    assert "--pull-policy missing" not in guide
    assert "--pull-policy always" in guide
    assert 'ghcr.io/aknochow/plaibook-ee@${digest}' in guide
    assert "cosign verify" in guide
    assert "refs/tags/v${tag}" in guide
    navigator = guide.index("ansible-navigator run review.yml")
    assert guide.rindex("cosign verify", 0, navigator) < navigator
    assert '"ghcr.io/aknochow/plaibook-ee@${digest}" &&' in guide
    assert "build/collections/*.tar.gz" not in section


def test_failed_cosign_verify_does_not_start_navigator(tmp_path: Path):
    guide = (ROOT / "docs" / "execution-environment.md").read_text(encoding="utf-8")
    start = guide.index("```bash\n") + len("```bash\n")
    script = guide[start : guide.index("```", start)]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "navigator-ran"
    (bindir / "podman").write_text(
        "#!/bin/sh\nif [ \"$1\" = image ]; then echo sha256:abc; fi\nexit 0\n",
        encoding="utf-8",
    )
    (bindir / "cosign").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (bindir / "ansible-navigator").write_text(
        f"#!/bin/sh\ntouch {marker}\nexit 0\n",
        encoding="utf-8",
    )
    for path in bindir.iterdir():
        path.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-c", script],
        check=False,
        cwd=tmp_path,
        env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert not marker.exists()


def test_pr_workflow_builds_both_arches_and_does_not_push():
    text = PR.read_text(encoding="utf-8")
    document = _load(PR)
    assert set(document["on"]) == {"pull_request", "workflow_call"}
    assert document["on"]["workflow_call"] is None
    assert document["permissions"] == {}
    job = document["jobs"]["build"]
    assert job["permissions"] == {"contents": "read"}
    arches = {row["arch"]: row["runner"] for row in job["strategy"]["matrix"]["include"]}
    assert arches == {"amd64": "ubuntu-latest", "arm64": "ubuntu-24.04-arm"}
    assert "packages:" not in text
    assert "id-token:" not in text
    assert "podman push" not in text
    assert "ansible-builder create" not in text
    assert "podman build" not in text
    assert "ansible-builder build" in text
    assert "--squash all" in text
    assert '--context "$RUNNER_TEMP/ee-context"' in text
    assert "--file execution-environment.yml" in text
    assert '--extra-build-cli-args "--platform linux/${{ matrix.arch }}"' in text
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
    assert "ea476312a97fc93d0ddefbfe7f6adaf1c184b1993cca8d8a33b424c08e9326fe" in ee
    assert "6755dc3d2c387510ff0b234470e54bf93a26dc525f93eb63c5c71018acc37d50" in ee


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
    assert "ansible-builder create" not in text
    assert "podman build" not in text
    assert "ansible-builder build" in text
    assert "--squash all" in text
    assert '--context "$RUNNER_TEMP/ee-context"' in text
    assert "--file execution-environment.yml" in text
    assert '--extra-build-cli-args "--platform linux/${{ matrix.arch }}"' in text
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


def test_every_ee_build_verifies_the_base_signature_first():
    key = (ROOT / "execution-environment-redhat-key.pem").read_text(encoding="utf-8")
    assert key.startswith("-----BEGIN PUBLIC KEY-----\n")
    script = (ROOT / "scripts" / "ee_verify_base.sh").read_text(encoding="utf-8")
    assert "--key \"${root}/execution-environment-redhat-key.pem\"" in script
    for path, job in ((PR, "build"), (PUBLISH, "build"), (RESCAN, "base")):
        names = [step["name"] for step in _load(path)["jobs"][job]["steps"]]
        verify = names.index("Verify the base image signature")
        assert names.index("Install cosign") < verify
        if "Build image" in names:
            assert verify < names.index("Build image")
    for path in (PR, PUBLISH):
        paths = _load(path)["on"]["pull_request" if path == PR else "push"]["paths"]
        assert "execution-environment-redhat-key.pem" in paths
        assert "scripts/ee_verify_base.sh" in paths


def test_rescan_gates_the_published_image_daily():
    document = _load(RESCAN)
    assert document["permissions"] == {}
    assert "schedule" in document["on"]
    assert "pull_request" not in document["on"]
    scan = document["jobs"]["scan"]
    assert scan["permissions"] == {"contents": "read", "security-events": "write"}
    assert scan["strategy"]["matrix"]["tag"] == ["latest", "release"]
    _assert_trivy_gate(scan)
    base = document["jobs"]["base"]
    assert base["permissions"] == {"contents": "read"}
    assert "execution-environment.yml" in RESCAN.read_text(encoding="utf-8")


def test_trivyignore_covers_only_the_pip_vendored_setuptools():
    document = _load(ROOT / ".trivyignore.yaml")
    findings = document["vulnerabilities"]
    ids = {item["id"] for item in findings}
    assert ids == {"CVE-2025-47273"}
    for item in findings:
        assert item["statement"] == "vulnerable module not present in the image"
        assert str(item["expired_at"]) == "2026-11-30"
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
    assert "https://github.com/aknochow/ansible-xai.git" in urls
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
