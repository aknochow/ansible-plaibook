# -*- coding: utf-8 -*-
"""Shared action plugin: detect project type from marker files.

Twelfth port for the action-plugin migration roadmap
(handoff.ansible-plaibook-action-plugin-full-migration-roadmap.yaml, item
port-detect-project) -- part of the coordinator-setup script family
rebuild (handoff.ansible-plaibook-coordinator-setup-rebuild-design.yaml).
Ports detect_project.py's detect_type(), which was previously spawned
as a CHILD PROCESS by prepare_briefing.py (subprocess.run([sys.executable,
detect_script, ...])) rather than called from Ansible directly -- this
port eliminates that whole extra-Python-process round trip, not just
relocates the logic.

detect_type() itself does real filesystem I/O (file existence checks,
a bounded content read of go.mod) against review_repo_path, which may
be on a delegated (sandboxed) host -- same "Ansible does I/O, the
plugin does pure logic" split as port-briefing-diff-fetching, not a
plugin that reaches across a delegated connection itself. Callers
gather file_exists/go_mod_content via plain ansible.builtin.stat/slurp
tasks (delegated to review_delegate_host) and pass them in.

Original Python (detect_project.py):
    def _is_k8s_operator(repo_path):
        go_mod = os.path.join(repo_path, "go.mod")
        project = os.path.join(repo_path, "PROJECT")
        if not os.path.isfile(go_mod) or not os.path.isfile(project):
            return False
        try:
            with open(go_mod) as f:
                return "sigs.k8s.io/controller-runtime" in f.read(1_048_576)
        except OSError:
            return False

    def _is_ansible_collection(repo_path):
        return (os.path.isfile(os.path.join(repo_path, "galaxy.yml"))
                or os.path.isfile(os.path.join(repo_path, "galaxy.yaml")))

    def _is_go_project(repo_path):
        return os.path.isfile(os.path.join(repo_path, "go.mod"))

    def _is_python_project(repo_path):
        return any(os.path.isfile(os.path.join(repo_path, f))
                   for f in ("pyproject.toml", "setup.py", "setup.cfg"))

    DETECTION_RULES = [
        {"type": "k8s-operator", "check": _is_k8s_operator},
        {"type": "ansible-collection", "check": _is_ansible_collection},
        {"type": "go-project", "check": _is_go_project},
        {"type": "python-project", "check": _is_python_project},
    ]

    def detect_type(repo_path):
        repo_path = os.path.realpath(repo_path)
        for rule in DETECTION_RULES:
            if rule["check"](repo_path):
                return rule["type"]
        return "unknown"

Rule order matters and is preserved exactly (first match wins) --
k8s-operator is checked before go-project specifically because a k8s
operator repo also has go.mod, so go-project's own broader check must
come after the more specific one, same as the original list order.

The 1MB bound on go.mod's content read (matching the original's
f.read(1_048_576)) is enforced by the CALLER's slurp task, not this
plugin -- ansible.builtin.slurp has no partial-read option, so the
caller truncates after decoding. go.mod files are practically never
anywhere near 1MB in real use; this bound exists to match the
original's own defensive read size, not because it's expected to ever
trigger.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase


def detect_project_type(file_exists: dict, go_mod_content: str | None) -> str:
    is_k8s_operator = (
        file_exists.get("go.mod", False)
        and file_exists.get("PROJECT", False)
        and go_mod_content is not None
        and "sigs.k8s.io/controller-runtime" in go_mod_content
    )
    if is_k8s_operator:
        return "k8s-operator"

    if file_exists.get("galaxy.yml", False) or file_exists.get("galaxy.yaml", False):
        return "ansible-collection"

    if file_exists.get("go.mod", False):
        return "go-project"

    if any(file_exists.get(name, False) for name in ("pyproject.toml", "setup.py", "setup.cfg")):
        return "python-project"

    return "unknown"


class ActionModule(ActionBase):
    """Detect project type from marker files -- real Python instead of a child-process spawn."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("file_exists", "go_mod_content"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        file_exists = self._task.args.get("file_exists")
        if file_exists is None:
            result["failed"] = True
            result["msg"] = "detect_project_type requires a 'file_exists' argument"
            return result

        result["changed"] = False
        result["type"] = detect_project_type(file_exists, self._task.args.get("go_mod_content"))
        return result
