# -*- coding: utf-8 -*-
"""Shared action plugin: detect diff domains for review steering.

Thirteenth port for the action-plugin migration roadmap (item
port-detect-project) -- detect_project.py's detect_domains(), same
child-process-elimination rationale as detect_project_type.py's module
docstring.

Content-matching rules need the CONTENT of changed files, which may
live on a delegated (sandboxed) host -- same "Ansible does I/O, the
plugin does pure logic" split as the rest of this port. The caller
gathers file_contents (a dict of only the diff files that exist and
were successfully read, bounded per-file the same way the original
bounds its own read) via ansible.builtin.stat/slurp tasks, and the
"new repo" sub-check's `git rev-list --count HEAD` becomes a plain,
safe (no interpolated untrusted input) Ansible command task, its
boolean result passed in as is_new_repo.

Original Python (detect_project.py), domain pattern table and
detect_domains() verbatim:
    DOMAIN_PATTERNS = [ ... see _DOMAIN_PATTERNS below, copied
      verbatim, not re-derived ... ]

    def _is_skill_definition(filepath):
        parts = filepath.split("/")
        if any(p in ("skills", "evals") for p in parts):
            return True
        return False

    def detect_domains(diff_files, repo_path=None):
        domains = set()
        for rule in DOMAIN_PATTERNS:
            if rule.get("content_match"):
                if repo_path:
                    for f in diff_files:
                        if _is_skill_definition(f):
                            continue
                        fpath = os.path.join(repo_path, f)
                        if os.path.isfile(fpath):
                            try:
                                with open(fpath) as fh:
                                    if any(p in fh.read(1_048_576) for p in rule["patterns"]):
                                        domains.add(rule["domain"])
                                        break
                            except OSError:
                                continue
            else:
                for f in diff_files:
                    if _is_skill_definition(f):
                        continue
                    if rule.get("basename_match"):
                        fname = "/" + f
                        if any(fname.endswith(p) or p in f for p in rule["patterns"]):
                            domains.add(rule["domain"])
                            break
                    elif any(p in f for p in rule["patterns"]):
                        domains.add(rule["domain"])
                        break

        if repo_path:
            # git rev-list --count HEAD <= 10 -> "new-repo"
            ...

        return sorted(domains)

Deliberate simplification, disclosed: the original's content-match
branch silently no-ops when `repo_path` is falsy (never reads any file
content at all in that case, so content-match rules can never fire).
This plugin has no equivalent "repo_path" concept -- file_contents is
either populated by the caller or it isn't; an empty dict produces the
same practical outcome (no content-match rule ever fires) without
needing a parallel repo_path-truthiness gate here. Every real caller
in ansible-plaibook always has a repo checkout to read from by this point in
the pipeline, so this distinction is never actually reachable either
way.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase

# Copied verbatim from detect_project.py's DOMAIN_PATTERNS -- not
# re-derived, so the two can be checked against each other by reading,
# not just by running tests.
_DOMAIN_PATTERNS = [
    {
        "domain": "ci-pipeline",
        "patterns": [
            ".gitlab-ci.yml", ".github/workflows/", "Jenkinsfile",
            ".circleci/", ".travis.yml", "azure-pipelines.yml",
        ],
    },
    {
        "domain": "python-packaging",
        "patterns": [
            "pyproject.toml", "setup.py", "setup.cfg",
            "MANIFEST.in", "__init__.py",
        ],
    },
    {
        "domain": "cli-entrypoint",
        "patterns": [
            "cli.py", "commands.py", "completions",
            "menu.py", "__main__.py",
        ],
    },
    {
        "domain": "cli-entrypoint",
        "patterns": [
            "case \"$", "case \"${", "subcommand", "argparse",
            "add_subparsers", "cobra.Command", "click.group",
        ],
        "content_match": True,
    },
    {
        "domain": "subprocess",
        "patterns": ["subprocess"],
        "content_match": True,
    },
    {
        "domain": "k8s-operator",
        "patterns": ["controller-runtime", "sigs.k8s.io/controller-runtime"],
        "content_match": True,
    },
    {
        "domain": "go-project",
        "patterns": [".go", "go.mod", "go.sum"],
    },
    {
        "domain": "openshift",
        "patterns": [
            "openshift.io", "kind: Route", "SecurityContextConstraints",
            "OAuthClient", "DeploymentConfig", "ImageStream",
            "BuildConfig", "ClusterServiceVersion",
        ],
        "content_match": True,
    },
    {
        "domain": "python-code",
        "patterns": [".py"],
    },
    {
        "domain": "shell-bash",
        "patterns": [".sh", "bash"],
    },
    {
        "domain": "ansible",
        "patterns": [
            "tasks/", "roles/", "plugins/", "playbooks/",
            "galaxy.yml", "galaxy.yaml", "meta/main.yml",
            "defaults/main.yml", "handlers/main.yml",
        ],
    },
    {
        "domain": "container",
        "patterns": [
            "Dockerfile", "Containerfile", ".containerfile",
            "docker-compose", "compose.yml", "compose.yaml",
        ],
    },
    {
        "domain": "api-endpoints",
        "patterns": [
            "/routes.py", "/views.py", "/handlers.go", "/router.go",
            "/controller.ts", "/routes.ts", "api/", "endpoints/",
        ],
        "basename_match": True,
    },
    {
        "domain": "database",
        "patterns": ["models.py", "migrations/", "schema.sql", ".sql"],
        "content_match": False,
    },
    {
        "domain": "database",
        "patterns": ["sqlalchemy", "django.db", "peewee", "gorm.io", "database/sql"],
        "content_match": True,
    },
    {
        "domain": "performance",
        "patterns": [
            "@lru_cache", "cache.get(", "cache.set(",
            "connection_pool", "pool_size", "batch_size",
            "paginate(", "async def ", "asyncio.",
        ],
        "content_match": True,
    },
    {
        "domain": "react-ui",
        "patterns": [".tsx", ".jsx"],
    },
    {
        "domain": "patternfly-ui",
        "patterns": ["@patternfly"],
        "content_match": True,
    },
]


def _is_skill_definition(filepath: str) -> bool:
    parts = filepath.split("/")
    return any(part in ("skills", "evals") for part in parts)


def detect_diff_domains(diff_files: list[str], file_contents: dict, is_new_repo: bool) -> list[str]:
    domains = set()
    for rule in _DOMAIN_PATTERNS:
        if rule.get("content_match"):
            for f in diff_files:
                if _is_skill_definition(f):
                    continue
                content = file_contents.get(f)
                if content is not None and any(p in content for p in rule["patterns"]):
                    domains.add(rule["domain"])
                    break
        else:
            for f in diff_files:
                if _is_skill_definition(f):
                    continue
                if rule.get("basename_match"):
                    fname = "/" + f
                    if any(fname.endswith(p) or p in f for p in rule["patterns"]):
                        domains.add(rule["domain"])
                        break
                elif any(p in f for p in rule["patterns"]):
                    domains.add(rule["domain"])
                    break

    if is_new_repo:
        domains.add("new-repo")

    return sorted(domains)


class ActionModule(ActionBase):
    """Detect diff domains for review steering -- real Python instead of a child-process spawn."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("diff_files", "file_contents", "is_new_repo"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        diff_files = self._task.args.get("diff_files")
        file_contents = self._task.args.get("file_contents")
        is_new_repo = self._task.args.get("is_new_repo")
        if diff_files is None or file_contents is None or is_new_repo is None:
            result["failed"] = True
            result["msg"] = "detect_diff_domains requires 'diff_files', 'file_contents', and 'is_new_repo' arguments"
            return result

        result["changed"] = False
        result["domains"] = detect_diff_domains(diff_files, file_contents, is_new_repo)
        return result
