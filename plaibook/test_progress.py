# -*- coding: utf-8 -*-
"""Map ansible task names to the spinner's coarse stages."""

from plaibook.progress import stage_for_task


def test_stage_for_task_main_pipeline():
    assert stage_for_task("Validate the selected provider runtime") == "setup"
    assert stage_for_task("Determine the repo clone URL") == "checkout"
    assert stage_for_task("Set up an OpenShell sandbox for isolated script execution") == "sandbox"
    assert stage_for_task("Scan the shared diff/PR-description content with ai-guardian") == "scan"
    assert stage_for_task("Dispatch the Security and Review lens agents") == "lenses"
    assert stage_for_task("review : Dispatch lenses via provider-native module (agent_family=xai)") == "lenses"
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


def test_sanitize_clone_url_strips_controls_and_bad_ports():
    from plaibook.progress import format_stage_line, sanitize_clone_url

    bell = "https://github.com/org/repo.git\x1b[31m\rINJECT"
    assert sanitize_clone_url(bell) == "https://github.com/org/repo.git"
    pretty = format_stage_line("checkout", clone_url=bell)
    assert pretty == "checkout (https://github.com/org/repo.git)"
    assert "\x1b" not in pretty
    assert "\r" not in pretty
    assert "INJECT" not in pretty
    assert sanitize_clone_url("not a url\nFAKE") == ""
    assert format_stage_line("checkout", clone_url="not a url") == "checkout"
    assert sanitize_clone_url("https://github.com:70000/org/repo.git") == ""
    assert format_stage_line("checkout", clone_url="https://github.com:70000/org/repo.git") == "checkout"
    csi = "https://github.com/org/repo.git\u009b[2J"
    cleaned_csi = sanitize_clone_url(csi)
    assert "\u009b" not in cleaned_csi
    assert cleaned_csi.startswith("https://github.com/org/repo.git")
    assert "\u009b" not in format_stage_line("checkout", clone_url=csi)
    bidi = "https://github.com/org/\u202erepo.git\u200b"
    assert sanitize_clone_url(bidi) == "https://github.com/org/repo.git"
    assert "\u202e" not in format_stage_line("checkout", clone_url=bidi)
    assert "\u200b" not in format_stage_line("checkout", clone_url=bidi)


def test_write_progress_line_only_cli_owned_tempfile(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    from plaibook.progress import (
        allowed_progress_path,
        create_progress_file,
        write_progress_line,
    )

    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    progress_dir, progress_path = create_progress_file()
    try:
        assert Path(progress_path).read_text(encoding="utf-8") == "setup\n"
        assert write_progress_line(progress_path, "lenses") is True
        assert Path(progress_path).read_text(encoding="utf-8") == "lenses\n"
        assert allowed_progress_path(progress_path) is not None

        outside = tmp_path / "passwd"
        outside.write_text("keep\n", encoding="utf-8")
        assert write_progress_line(str(outside), "overwrite") is False
        assert outside.read_text(encoding="utf-8") == "keep\n"

        relative = "plaibook-progress-nope.txt"
        assert allowed_progress_path(relative) is None
        assert write_progress_line(relative, "x") is False

        link = Path(progress_dir) / "plaibook-progress-link.txt"
        link.symlink_to(outside)
        assert write_progress_line(str(link), "pwn") is False
        assert outside.read_text(encoding="utf-8") == "keep\n"

        os.chmod(progress_dir, 0o755)
        assert write_progress_line(progress_path, "after-mode") is False
        assert Path(progress_path).read_text(encoding="utf-8") == "lenses\n"
    finally:
        Path(progress_path).unlink(missing_ok=True)
        Path(progress_dir, "plaibook-progress-link.txt").unlink(missing_ok=True)
        os.chmod(progress_dir, 0o700)
        Path(progress_dir).rmdir()


def test_write_progress_line_accepts_cache_scratch(tmp_path, monkeypatch):
    from pathlib import Path

    from plaibook.playbook import runtime_tmp_dir
    from plaibook.progress import (
        allowed_progress_path,
        create_progress_file,
        write_progress_line,
    )

    home = tmp_path / "home"
    home.mkdir()
    proc_tmp = tmp_path / "proc-tmp"
    proc_tmp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("tempfile.tempdir", str(proc_tmp))
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(proc_tmp))

    scratch = runtime_tmp_dir(home)
    progress_dir, progress_path = create_progress_file(directory=str(scratch))
    try:
        assert Path(progress_dir).parent == scratch
        assert Path(progress_path).read_text(encoding="utf-8") == "setup\n"
        assert allowed_progress_path(progress_path) is not None
        assert write_progress_line(progress_path, "lenses") is True
        assert Path(progress_path).read_text(encoding="utf-8") == "lenses\n"
        outside = tmp_path / "plaibook-progress-nope.txt"
        outside.write_text("keep\n", encoding="utf-8")
        assert write_progress_line(str(outside), "overwrite") is False
        assert outside.read_text(encoding="utf-8") == "keep\n"
    finally:
        Path(progress_path).unlink(missing_ok=True)
        Path(progress_dir).rmdir()

