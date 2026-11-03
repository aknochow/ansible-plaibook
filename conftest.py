"""Root pytest configuration shared by every test directory.

Tests that build throwaway git repositories must not pick up the
developer's global or system git config (commit/tag signing, hooks,
templates, identity). The identity and config isolation below applies
for the whole session and is restored afterwards.
"""

import os

import pytest

_HERMETIC_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "test-author@example.com",
    "GIT_COMMITTER_NAME": "Test Committer",
    "GIT_COMMITTER_EMAIL": "test-committer@example.com",
}


@pytest.fixture(scope="session", autouse=True)
def _hermetic_git_config():
    mp = pytest.MonkeyPatch()
    for key, value in _HERMETIC_GIT_ENV.items():
        mp.setenv(key, value)
    try:
        yield
    finally:
        mp.undo()
