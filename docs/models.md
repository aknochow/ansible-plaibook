---
type: Guide
title: Models and reasoning effort
description: The default model and effort for each provider, which calls each effort setting reaches, and how to change them.
tags: [models, reasoning-effort, openai, gemini, xai, cursor, claude]
status: stable
---

# Models and reasoning effort

Each provider has its own model and effort variables. A setting for one
provider does nothing for another: `review_openai_lens_reasoning_effort`
has no effect on an xAI or Cursor review.

A review makes three kinds of model call:

- **Lenses.** Two structured calls, Security and Functionality/Quality, that read the diff and return findings. Most of the cost is here.
- **Explore.** Up to `review_explore_max_turns` turns that read files beyond the diff with read-only tools. PR reviews only.
- **Verify.** An independent re-check of each Critical and Major finding.

Verify sometimes makes a fourth call, the **continuity audit**. When a
re-review claims a finding continues one from the previous round, a
separate single call checks that the claim matches the real prior
finding. Every provider runs it, and it uses the verify model.

Some providers apply the effort setting to all of these, and some only
to the lenses. The tables below say which.

## Starting settings

| Provider (`agent_family`) | Model | Effort | Start here when |
|---|---|---|---|
| `openai` | `gpt-6-luna` | API default for the lenses | You want the cheapest reliable review. The GitHub review check uses this. |
| `xai` | `grok-4.7` | `high` | You want a deeper review and accept a higher cost per run. |
| `cursor` | `grok-4.7` | `high` | You already pay for Cursor. |
| `gemini` | `gemini-3.6-flash` | thinking `high` | You use the AI Studio free tier or Vertex. |
| `claude` | `claude-opus-4-6`, verify `claude-haiku-4-5` | no effort setting | You use the Anthropic API or Vertex. |

On this repository's pull requests, a `gpt-6-luna` review at these
defaults costs about 0.1 to 1 cent.

Save a provider once, and `plai review` uses it from then on:

```bash
plai review org/repo/123 --provider openai
```

That writes `agent_family` to `~/.config/ansible-plaibook/vars.yml`. Put
any variable on this page in the same file to keep it, or pass it with
`-e` for one run. `-e` wins.

## OpenAI (`agent_family: openai`)

| Call | Model | Effort |
|---|---|---|
| Lenses | `review_openai_model` | `review_openai_lens_reasoning_effort`. Empty sends no effort, so the API default applies. |
| Explore and verify | `review_explore_openai_model`, `review_verify_openai_model` (both default to `review_openai_model`) | `none` for `gpt-6-luna` and `gpt-5.6`, because Chat Completions refuses function tools with any other effort. The API default for other models. |
| Continuity audit | `review_verify_openai_model` | `review_openai_continuity_reasoning_effort`, which defaults to the lens effort. |

The lens effort accepts `low`, `medium`, `high`, `xhigh`, or `max`.
Higher effort costs more and takes longer. It does not reliably find
more: the same diff can produce different findings at different levels,
so run it twice before you decide one level is better.

```yaml
# ~/.config/ansible-plaibook/vars.yml
agent_family: openai
review_openai_model: gpt-6-luna
review_openai_lens_reasoning_effort: medium
```

```bash
plai review org/repo/123 -e review_openai_lens_reasoning_effort=high
```

`grok-4.7` works with this family too. It goes to the OpenAI-compatible
endpoint at `https://api.x.ai/v1`, not through the xAI SDK. Prefer
`agent_family: xai` for Grok.

### Moving from gpt-5.6 to gpt-6-luna

- `gpt-5.6` lenses ran at effort `none` unless you set one. `gpt-6-luna` lenses use the API default, so with no setting a review reasons more, and takes longer and costs more, than it did on `gpt-5.6`.
- A `review_openai_lens_reasoning_effort` you already saved carries over unchanged.
- Explore and verify stay at `none` on both models.
- `gpt-5.6` with no suffix resolves to the larger, more expensive `gpt-5.6-sol`.

## xAI (`agent_family: xai`)

| Call | Model | Effort |
|---|---|---|
| Lenses, explore, verify | `review_xai_model`, `review_explore_xai_model`, `review_verify_xai_model` | `review_xai_lens_reasoning_effort`, default `high`, for all three. |
| Continuity audit | `review_verify_xai_model` | `review_xai_continuity_reasoning_effort`, which defaults to the lens effort. |

Calls go through `aknochow.xai.chat`, which uses the xAI SDK's
`chat.stream`. A high-effort lens on a large diff can run for many
minutes. `xai_timeout` (default 1800 seconds) bounds each call,
including the module's own retries.

```yaml
agent_family: xai
review_xai_model: grok-4.7
review_xai_lens_reasoning_effort: high
```

## Cursor (`agent_family: cursor`)

| Call | Model | Effort |
|---|---|---|
| Lenses, explore, verify | `review_cursor_model`, `review_explore_cursor_model`, `review_verify_cursor_model` | `review_cursor_effort`, default `high`, for all three and the continuity audit. `aknochow.cursor` sends it as the model's `effort`. |

```yaml
agent_family: cursor
review_cursor_model: grok-4.7
review_cursor_effort: high
```

## Gemini (`agent_family: gemini`)

| Call | Model | Effort |
|---|---|---|
| Lenses, explore, verify | `review_gemini_model`, `review_explore_gemini_model`, `review_verify_gemini_model` (verify defaults to `gemini-3.5-flash`) | `review_gemini_thinking_level`, default `high`, for all three and the continuity audit. |

Thinking and visible output share Gemini's output budget, so a higher
thinking level needs the larger completion budget the defaults already
set. In one measured run, `minimal` spent no thinking tokens and `high`
spent about 3,250. Both found the same Critical finding, so the level
trades cost and output headroom rather than correctness. On the AI
Studio free tier, thinking tokens cost nothing.

```yaml
agent_family: gemini
review_gemini_model: gemini-3.6-flash
review_gemini_thinking_level: high
```

## Claude (`agent_family: claude`)

| Call | Model | Effort |
|---|---|---|
| Lenses and explore | `review_agent_model`, default `claude-opus-4-6` | None. Extended thinking is not enabled on this path. |
| Verify and the continuity audit | `review_verify_model`, default `claude-haiku-4-5` | None. |

`agent_family: claude_cli` runs the local `claude` CLI on your
subscription instead of the API. Its effort is `review_claude_cli_effort`,
default `high`, and it applies to the continuity audit too.

## Check what a run used

The review summary names the provider, collection, model, and effort:

```text
openai · aknochow.openai@3657a67 · gpt-6-luna · effort lens default, tools none · 5 agents · $0.0055
```

`effort lens default` means no lens effort was sent. `tools none` is the
effort explore and verify used. Every variable on this page, with its
environment variable, is in the [variable reference](reference.md#model-selection).
