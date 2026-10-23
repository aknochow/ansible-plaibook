---
type: Reference
title: Gotchas
description: Vertex structured output, sandbox network policy, execution-environment pins, and the OpenShell gateway URL.
tags: [gotchas, vertex, sandbox, openshell]
status: stable
---

# Gotchas

- **Vertex org policy.** `constraints/vertexai.allowedPartnerModelFeatures` can block `output_config` (native structured output) on some GCP projects while plain messages and forced tool use still work on the same project and model. This pipeline uses `tool_choice`-forced structured output for that reason. See `roles/review/tasks/lenses.yml`.
- **Sandbox network policy is separate from provider attachment.** A sandbox cannot be assumed to reach an internal GitLab or an arbitrary API because a provider is attached. Check the gateway network policy. See [Sandboxes and LLM calls](sandbox-and-agent-safety.md).
- **EE collections follow `collections-requirements.yml`.** The image installs those git pins. It does not contain OpenShell `ssh_proxy.py` or certs. A change to that file on `main` rebuilds `ghcr.io/aknochow/plaibook-ee:main` and `:latest`.
- **The review uses the OpenShell CLI's selected gateway.** The gateway name defaults to `openshell`. When `~/.config/openshell/gateways/openshell/metadata.json` exists, its `gateway_endpoint` is the sandbox URL. On a laptop that is often `https://localhost:17670`. `https://host.openshell.internal:17670` is the fallback when that file is absent. Override either with `-e sandbox_gateway=...` or `-e sandbox_gateway_name=...`.
