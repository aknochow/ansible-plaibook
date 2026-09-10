---
type: Reference
title: Variable Reference
description: Every variable that controls a review.yml run, its default, and what it changes.
tags: [reference, configuration, variables]
status: stable
---

# Variable Reference

## Selecting what to review

| Variable | Default | Purpose |
|---|---|---|
| `review_type` | `pr` | `pr` \| `branch` \| `commit`. Selects which target-resolution path runs. |
| `review_targets_raw` | n/a | A PR/MR URL, `org/repo#N` / `org/repo!N`, or a newline-separated list of them. `review_type: pr` only. |
| `commit_sha` | `HEAD` | `review_type: commit` only. |
| `repo_path` | current directory | `review_type: commit` only. |
| `branch_review_target` | n/a | `review_type: branch` only. A local path or remote branch reference. |

## Run behavior

| Variable | Default | Purpose |
|---|---|---|
| `use_sandbox` | `true` for `pr`/`branch`, `false` for `commit` | Runs the target-repo checkout and checklist execution inside an OpenShell sandbox. |
| `post_results` | `false` | Posts the rendered review back to the real PR/MR. Reviewing is safe to automate; posting is a write to shared state and needs explicit opt-in. |
| `fail_on_regressions` | `false` for `pr`/`branch`, `true` for `commit` | Whether a `NEEDS_CHANGES` verdict with a real Critical/Major finding makes the Ansible run itself exit non-zero. Lets a `commit_review` invocation gate a hook on the exit code directly. |
| `review_same_commit_fast_path_enabled` | `true` | Skips lens dispatch, merge, and persistence entirely when the target's current commit matches the last-reviewed one, a zero-LLM-cost check before spending anything. |
| `review_explore_max_turns` | `3` for `pr`/`branch`, `0` for `commit` | How many additional turns the exploration pass gets to look beyond the diff itself. |

## Model selection

| Variable | Default | Purpose |
|---|---|---|
| `agent_family` | `claude` (or operator config) | Provider dispatch family: `claude`, `gemini`, `openai`, or `cursor`. Committed default is `claude`. Per-machine override (none of these are in git): `$XDG_CONFIG_HOME/ansible-plaibook/vars.yml` (or `~/.config/ansible-plaibook/vars.yml`) when that file exists, else gitignored `host_vars/localhost.yml`, else `ANSIBLE_REVIEW_AGENT_FAMILY`. Extra-vars (`-e`) still win. When `agent_family=cursor` and `CURSOR_AGENT=1` with no attach env, `review.yml` starts `aknochow.cursor.bridge` (double-fork sidecar) and reaps it in `always`. Already-set `CURSOR_SDK_BRIDGE_URL`+token is left alone. iTerm/CI/AAP keep in-process `launch_bridge`. |
| `review_agent_model` | `claude-opus-4-6` | Model used for the Security and Functionality/Quality lens dispatch. |
| `review_openai_model` | `gpt-5.6` when `agent_family=openai` | OpenAI model for both lenses; the unsuffixed GPT-5.6 alias resolves to Sol. Set `OPENAI_API_KEY` for hosted OpenAI use. |
| `review_openai_max_completion_tokens` | `16384` | Hosted OpenAI completion budget for lens, exploration, and verification calls. |
| `review_openai_lens_reasoning_effort` | API default | Optional explicit `reasoning_effort` for hosted OpenAI lens calls, such as `low`, `medium`, `high`, `xhigh`, or `max`. Explore/verify tool calls remain on the hosted tool-compatible `none` setting. |
| `review_openai_continuity_max_completion_tokens` | `review_openai_max_completion_tokens` | Completion budget for the no-tool continuity audit; prevents reasoning models from exhausting a small structured-output budget before returning JSON. |
| `review_explore_tool_timeout_seconds` | `120` | Per-search wall-clock ceiling. A failed or timed-out search fails the review instead of being treated as an empty result. |
| `review_explore_max_tool_calls` | `12` | Total read-only exploration tool calls per target. |
| `review_verify_model` | `claude-haiku-4-5` | Model used for the independent Critical/Major verification pass, deliberately a cheaper tier than the lens dispatch, since verification is lower-stakes per call and runs more often. |

## Operator context

| Variable | Default | Purpose |
|---|---|---|
| `review_extra_notes` | n/a | Free-text, run-only, **trusted** operator context threaded into every lens's system prompt (unlike the diff itself, which is untrusted). Not persisted anywhere. Guidance, not an override. A note claiming something is fine doesn't exempt it from the validation chain if it plainly isn't. |

## Sandbox/cleanup

| Variable | Default | Purpose |
|---|---|---|
| `cleanup_sandbox_onfail` | `true` | Tears the sandbox down even after a failed run. Set `false` to leave a failed run's sandbox up for debugging. |
| `sandbox_tls_source` | n/a | Overrides the sandbox's TLS source when the default doesn't apply. |
