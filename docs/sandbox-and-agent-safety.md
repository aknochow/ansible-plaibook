---
type: Concept
title: Sandboxes and LLM Calls
description: The production-verified pattern for combining OpenShell sandboxes with Claude/Gemini calls, and the pattern deliberately not used.
tags: [sandbox, security, architecture, openshell]
status: stable
---

# Sandboxes and LLM calls: how to structure playbooks safely

This documents the pattern `review.yml` already runs in production
(verified live against two separate real OpenShift clusters) for
combining OpenShell sandboxes with Claude/Gemini calls, and, just as
importantly, the pattern it deliberately does NOT use. Follow this when
adding a new sandboxed capability rather than re-deriving it from
OpenShell's own community examples, which demonstrate a different,
not fully working, approach. See "What we don't do" below.

## The core principle

**LLM decision-making stays on the controller. Sandboxes are for
isolated, non-agentic script/tool execution only.**

`aknochow.claude.message` and `aknochow.gemini.generate` are thin
wrappers around the raw Anthropic/Google GenAI SDKs: a single
request-in, text-out API call. They have no filesystem access, no
shell execution, no tool-use loop of their own. Whatever the model
returns is just a string; the *playbook* decides what to do with it.
That's what "narrow blast radius" means concretely: even if a call's
input includes untrusted content (a PR description, a diff, a file
from the sandbox), the worst case is a bad string coming back, not an
autonomous process taking further action with real credentials.

Contrast that with running a full agent CLI (`claude`, `opencode`,
`goose`, ...) autonomously *inside* a sandbox, wired to real API
credentials via OpenShell's provider-injection mechanism. That agent
can read files, run commands, and make further decisions based on
content it encounters, a much larger blast radius, and (see below)
not something this codebase actually relies on.

## The proven pattern

Reference implementation: `roles/review/tasks/setup_sandbox.yml`,
`roles/review/tasks/teardown_sandbox.yml`, `roles/review/tasks/explore_turn.yml`,
and `review.yml`'s `review_delegate_host` computation.

1. **Create the sandbox** via `aknochow.openshell.sandbox` (`state:
   present`). No `delegate_to`: this and every other `aknochow.openshell.*`
   module call talks directly to the gateway's gRPC API from the
   controller; there's nothing to delegate.

2. **Register the sandbox as a real Ansible inventory host**, over SSH,
   using the gateway's own `ssh_proxy.py` as the `ProxyCommand` (it
   speaks the sandbox's gRPC exec channel underneath, not a real SSH
   daemon):

   ```yaml
   - ansible.builtin.add_host:
       name: sandbox_target
       ansible_host: "{{ sandbox.sandbox.name }}"
       ansible_user: sandbox
       ansible_connection: ssh
       ansible_python_interpreter: auto_silent   # see gotcha below
       ansible_ssh_common_args: >-
         -o ProxyCommand="{{ ssh_proxy_python | quote }} {{ ssh_proxy_script | quote }}
         --gateway {{ sandbox_gateway | quote }} ...
         --sandbox {{ sandbox.sandbox.name | quote }} --workspace {{ sandbox.sandbox.workspace | quote }}"
         -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ...
   ```

   This is what actually unlocks "safe agent interaction" as a
   *practical* matter: once the sandbox is a normal inventory host, any
   ordinary Ansible module (`ansible.builtin.command`,
   `ansible.builtin.git`, `ansible.builtin.copy`, whatever the task
   needs) works inside it via plain `delegate_to`, with no bespoke
   wrapper module required per operation.

3. **Delegate individual tasks to it conditionally**, via a single
   computed variable:

   ```yaml
   review_delegate_host: "{{ 'sandbox_target' if (use_sandbox | bool) else '' }}"
   ...
   delegate_to: "{{ review_delegate_host | default(omit, true) }}"
   ```

   The `default(omit, true)` second argument matters: it treats an
   *empty string* (the unsandboxed case) the same as undefined,
   omitting `delegate_to` entirely so the task runs on the controller.
   One variable, one pattern, reused on every task that needs to run
   "wherever the sandbox is, if there is one."

4. **Use `aknochow.openshell.sandbox_upload` for bulk file transfer**,
   not SSH. It goes over the sandbox's own gRPC exec channel (chunked
   stdin), not SSH/SFTP, confirmed live to drop a 290-file/4.2MB
   checkout from multiple minutes (SSH-delegated `ansible.builtin.copy`)
   to under a second. Also not delegated, same reasoning as sandbox
   create/delete: it talks directly to the gateway.

5. **`aknochow.claude.message`/`aknochow.gemini.generate` calls are
   never delegated.** They always run on the controller. This is the
   deliberate choice that keeps the "safe SDK" pattern's blast-radius
   guarantee intact. A task that needs both sandboxed context (e.g. a
   diff computed inside the sandbox) and a Claude call reads the
   sandboxed result back to the controller first (via `register` on the
   delegated task), then calls `aknochow.claude.message` as a normal,
   non-delegated task using that value.

6. **Secrets go into files, never argv or bare `env VAR=value`.** The
   sandbox bearer token is written to a `0600` file and passed via
   `--bearer-token-file`, not as a CLI arg (visible in `/proc/*/cmdline`
   for the process's whole lifetime) or an `env VAR=value` wrapper
   (narrows but doesn't eliminate exposure, see `roles/review/tasks/setup_sandbox.yml`'s
   inline comment for the full reasoning, including why this needed
   `ssh_proxy.py --bearer-token-file` support to exist at all).

7. **Tear down in `block`/`always`.** `roles/review/tasks/teardown_sandbox.yml`
   deletes the sandbox in the `block` and removes the bearer-token file
   in `always`, so a failed delete RPC (network blip, etc.) doesn't also
   leak the token file for the rest of the pod's lifetime.

### Gotcha: `ansible_python_interpreter: auto_silent`

Without it, `delegate_to: sandbox_target` tasks silently inherit the
*controller's* `ansible_python_interpreter` override (e.g. a local venv
path set in `host_vars/localhost.yml`) instead of discovering the
sandbox's own interpreter, a well-known Ansible gotcha where
`ansible_python_interpreter` doesn't automatically re-resolve per
delegated host. `auto_silent` forces real discovery inside the sandbox.

## What we don't do

OpenShell's own community example (`ansible-openshell`'s
`examples/multi_agent.yml`) demonstrates a different pattern: attach a
`google-vertex-ai`-typed `aknochow.openshell.provider` to a sandbox and
run a pre-installed agent CLI (`claude`, `opencode`) inside it,
expecting it to pick up the injected credential automatically.

Verified live against a real gateway that this does not work out of
the box against the standard `base:latest` sandbox image:

- The injected credential is a resolve-placeholder
  (`service_account_json=openshell:resolve:env:v...`), not a real
  secret, confirmed this is a genuine network-layer credential relay
  (the sandbox process never sees the raw value), which is a sound
  design, but:
- `claude` CLI doesn't auto-detect it and asks for interactive login.
- `opencode` only lists its own built-in free-tier models, nothing
  vertex-backed is registered.
- `goose` (the tool the injected `GOOSE_PROVIDER=gcp_vertex_ai` env var
  actually targets) isn't installed in the base image at all.

None of this is what `review.yml` needs anyway. Per the principle
above, we never want an autonomous agent making its own tool-use
decisions inside a sandbox with live credentials. If a future use case
genuinely needs that, treat it as new platform-integration work (likely
requiring a different sandbox image and/or additional
provider-`config` keys not documented anywhere we've found), not an
extension of the pattern in this doc, and budget real live-testing
time for it, the same way this doc's own patterns were verified against
two separate real OpenShift clusters rather than assumed from source or
examples.

## Quick checklist for a new sandboxed task

- [ ] Sandbox create/delete/upload: `aknochow.openshell.*`, no `delegate_to`
- [ ] Anything else that needs to run "in the sandbox": plain Ansible
      module + `delegate_to: "{{ review_delegate_host | default(omit, true) }}"`
- [ ] Claude/Gemini calls: `aknochow.claude.message`/`aknochow.gemini.generate`,
      never delegated, always controller-side
- [ ] Secrets: file + path argument, `mode: "0600"`, cleaned up in `always`
- [ ] Sandboxed result needed by a Claude call? `register` it on the
      delegated task, read the registered var back on the controller
