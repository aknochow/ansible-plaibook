"""The OpenAI lens's async limit follows openai_timeout instead of a fixed 600s."""

from pathlib import Path

import jinja2
import yaml

ROLE = Path(__file__).resolve().parent.parent / "roles" / "review"


def _defaults() -> dict:
    return yaml.safe_load((ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))


def _render(expr: str, **values) -> str:
    return jinja2.Environment().from_string(expr).render(**values)


def test_async_limit_covers_every_sdk_attempt():
    expr = _defaults()["openai_lens_async_seconds"]
    assert _render(expr, openai_timeout=300.0) == "960"
    assert _render(expr, openai_timeout=1200.0) == "3660"


def test_lens_jobs_and_wait_use_the_derived_limit():
    tasks = yaml.safe_load((ROLE / "tasks" / "dispatch_lens_openai.yml").read_text(encoding="utf-8"))
    jobs = [t for t in tasks if t.get("poll") == 0]
    assert len(jobs) == 2
    assert {t["async"] for t in jobs} == {"{{ openai_lens_async_seconds }}"}
    wait = next(t for t in tasks if "ansible.builtin.async_status" in t)
    retries = _render(wait["retries"], openai_lens_async_seconds="960")
    assert int(retries) * wait["delay"] >= 960
