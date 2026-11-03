"""The Cursor lens wait must not give up before the async limit.

dispatch_lens_cursor.yml starts each lens with an async limit and polls
it with async_status. A poll budget (retries * delay) shorter than the
async limit abandons a lens that is still running. These tests render
the real task expressions for several timeouts.
"""

from pathlib import Path

import jinja2
import pytest
import yaml

ROLE = Path(__file__).resolve().parent.parent / "roles" / "review"
TASKS = yaml.safe_load((ROLE / "tasks" / "dispatch_lens_cursor.yml").read_text())
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
STARTS = [t for t in TASKS if "async" in t]
WAITS = [t for t in TASKS if "ansible.builtin.async_status" in t]


def render(value, **variables):
    return jinja2.Environment().from_string(str(value)).render(**variables)


def test_both_lenses_started_and_waited():
    assert len(STARTS) == 2
    assert len(WAITS) == 2


def test_default_is_env_overridable_and_above_old_limit():
    expr = DEFAULTS["review_cursor_lens_timeout"]
    assert "ANSIBLE_REVIEW_CURSOR_LENS_TIMEOUT" in expr
    assert "default(1800, true)" in expr
    assert DEFAULTS["review_cursor_lens_poll_delay"] > 0


@pytest.mark.parametrize("timeout", [1, 7, 899, 900, 901, 1800, 1801, 3600])
def test_poll_budget_covers_async_limit(timeout):
    variables = {
        "review_cursor_lens_timeout": timeout,
        "review_cursor_lens_poll_delay": DEFAULTS["review_cursor_lens_poll_delay"],
    }
    for start in STARTS:
        assert int(render(start["async"], **variables)) == timeout
    for wait in WAITS:
        retries = int(render(wait["retries"], **variables))
        delay = int(render(wait["delay"], **variables))
        assert retries * delay >= timeout


def test_no_hardcoded_limits_left():
    for task in STARTS + WAITS:
        for key in ("async", "retries", "delay"):
            if key in task:
                assert "review_cursor_lens" in str(task[key])
