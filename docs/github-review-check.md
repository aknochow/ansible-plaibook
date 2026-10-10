---
type: Guide
title: GitHub plaibook review check
description: The pull-request check that runs plai review after the other checks pass and posts one review.
tags: [github-actions, review]
status: stable
---

# GitHub plaibook review check

The reusable workflow has two jobs. **`wait`** polls the other checks. **`review`** starts after that job passes, so its duration is the review itself. GitHub shows them as **`plai / wait`** and **`plai / review`**. The trigger is `pull_request_target`, so GitHub reads this workflow from the base branch and reports the job against that branch. A pull request cannot replace the file that receives the secrets. Requiring `plai / review` on the pull request head does not see this job. The gate ignores `plai`, `plai / wait`, `plai / review`, and `plaibook review` only when the Actions run is `.github/workflows/plai-review.yml`. The same display name from another workflow still blocks. Reruns of one workflow file collapse. Two workflows that publish the same check name do not.

It runs on the GitHub-hosted runner. It does not call an Automation Controller. `post_results` stays `false`. The workflow posts the pull-request review itself.

The review starts on the pull request and waits until the other checks on that commit have passed. A failed check stays failed. A green `plaibook review` does not cover it. `skipped` and `neutral` checks do not block the review. In-progress checks do. The workflow job is the check. It does not call the Checks API.

The workflow runs:

```bash
plai review org/repo/N --json --force -e review_require_ci_passing=false
```

The GitHub-hosted runner installs OpenShell v0.1.2 from the installer at commit `6648bd0c290efbc41ba131ee9831ee45cd431f94`, after checking its sha256. That installer starts a local gateway. The review sandbox image is `ghcr.io/nvidia/openshell-community/sandboxes/base` pinned by digest. That image embeds a policy the local gateway cannot activate: the sandbox stays in `ConfigurationInvalid` until the 300-second repair window expires. The workflow sets a gateway-global filesystem policy with no network rules, which replaces the image policy, then creates one sandbox from that digest and deletes it. `plai review` uses the same digest and waits up to 600 seconds for it to become ready. Model calls stay on the controller, so the guest does not need network rules. `review_require_ci_passing=false` is set because this workflow already required the other checks to pass, and a previous `plaibook review` failure must not skip the re-run. The product default for `post_results` and for `review_require_ci_passing` is unchanged.

`NEEDS_CHANGES` is submitted as one pull request review with event `REQUEST_CHANGES`. `READY_FOR_HUMAN_REVIEW` is submitted as one review with event `COMMENT`. The workflow does not approve. Inline comments are part of that review. Python renders each comment from the finding fields. The agent does not write the comment body. Every posted finding that sets `replacement` must set it to the exact new source for `start_line` through `line`. That comment contains one `suggestion` block, and the Files changed tab offers **Commit suggestion**. A finding whose fix is not one contiguous edit sets `replacement` to an empty string. The review summary lists every finding as a numbered Markdown list, with a blank line before the next item. A finding that also has a suggestion block says to see the suggested fix below. A replacement that is prose, or a multi-line replacement whose `start_line` is missing or outside `1` through `line`, is dropped before posting. A multi-line replacement with `start_line` equal to `line` replaces that one line and is posted on the single-line anchor. The finding stays in the summary, and the play continues. A later run on the same commit does not post a second review when that summary and review state are already present. Cost and token counts are not part of that comparison. It updates a comment that has the same `<!-- plaibook-finding:... -->` marker instead of stacking a copy. Only a comment or review authored by `plai-review[bot]` is updated or retired.

The GitHub App slug is `plai-review`. The review author is `plai-review[bot]`. Grant the app Pull requests write, install it on the repository, and set the Actions secrets `PLAI_GITHUB_APP_ID` and `PLAI_GITHUB_APP_PRIVATE_KEY`. The private key is used only to mint a short-lived installation token for the publish step, after the agent has finished. Checkout and the check gate keep using the read-only `GITHUB_TOKEN`.

Re-run the review with a pull-request comment that starts with `/plai-review`, or with `workflow_dispatch` from the default branch and the pull request number. `workflow_dispatch` from any other ref is ignored. A `pull_request` event does not start a review. Comments from bots, and from users who are not OWNER, MEMBER, or COLLABORATOR, are ignored. `pull_request_target` reviews only a head in this repository. A fork stays skipped until that comment, and the comment reviews it only when the pull request author is OWNER, MEMBER, or COLLABORATOR.

The gate reads check runs on the pull request head and on the merge commit. When both commits have other checks, both must pass. In-progress checks wait. An empty suite that has not completed waits, except the cursor app, which stays queued with no runs on this repository. An empty completed suite fails unless its conclusion is success, skipped, or neutral. A commit with no other checks passes, and the review still runs. The review is published against the head SHA.

The job token is `contents: read`, `pull-requests: read`, and `checks: read`. `checks: read` is what the gate uses to list check runs on a private repository. The token cannot write the pull request. The publish step runs after the agent and posts with the GitHub App installation token. The review step installs OpenShell and uses the default sandbox. `CURSOR_API_KEY` is present in the review step because the Cursor SDK call runs on the controller. `PLAI_GITHUB_APP_ID` and `PLAI_GITHUB_APP_PRIVATE_KEY` are required to post.

The review runs as cursor. `CURSOR_API_KEY`, `PLAI_GITHUB_APP_ID`, and `PLAI_GITHUB_APP_PRIVATE_KEY` are secrets on the `plaibook-review` environment. That environment's deployment branches are `main` only. The called review job sets `environment: plaibook-review` with `deployment: false`, so the branch rule and secrets apply and no deployment record is written. A caller job cannot: GitHub rejects `environment` next to `uses`. `pull_request_target` evaluates the deployment rule against the default branch, so the review receives the keys. A `pull_request` workflow is evaluated against `refs/pull/N/merge` and does not. The caller passes only `CURSOR_API_KEY`, `PLAI_GITHUB_APP_ID`, and `PLAI_GITHUB_APP_PRIVATE_KEY`. It does not inherit the rest of the repository or organization secrets. These names must not also be repository secrets. A same-repository `pull_request` workflow can read repository secrets. If `CURSOR_API_KEY` is missing, the check fails. It does not succeed when no review ran.

The check is `success` when the review ran and was posted. `NEEDS_CHANGES` is still a pull request review with event `REQUEST_CHANGES`, and the job records a warning annotation for that verdict. The job fails when the review did not run or did not post. The verdict is the review, not a failed check.

Callers in other repositories pin the reusable workflow to a full commit SHA. The caller grants read access. The called job does not raise it. The app secrets post the review.

```yaml
jobs:
  review:
    permissions:
      contents: read
      pull-requests: read
      checks: read
    uses: aknochow/ansible-plaibook/.github/workflows/plai-review-run.yml@<40-character-sha>
    secrets:
      CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}
      PLAI_GITHUB_APP_ID: ${{ secrets.PLAI_GITHUB_APP_ID }}
      PLAI_GITHUB_APP_PRIVATE_KEY: ${{ secrets.PLAI_GITHUB_APP_PRIVATE_KEY }}
```

Callers do not pass a repository or a ref. The reusable workflow checks out `aknochow/ansible-plaibook` at commit `2cfe75b24d9619b793757914a57d5cb5284b3739`. This repository calls the workflow at commit `4490fc66b59af3c81b9d689cdd7c639a3f739d71`. The called review job is the one that sets `environment: plaibook-review`. The caller passes only the three environment secret names. The review job installs that tree with `uv sync --locked`. `GITHUB_SHA` on `pull_request_target` is the base branch and is not the tools pin. The pull request head is the commit under review. It is not checked out. The publisher that receives the GitHub App private key is the copy taken from that pinned revision before `plai review` starts.

Without the GitHub App secrets the publish step fails. The job token cannot post the review.

Do not pass that secret to a `uses:` ref of `@main`. This repository calls the workflow at a full commit SHA. This change does not edit branch protection. After it is on the default branch, require the check name `plai / review`. The GitHub App slug is `plai-review`.
