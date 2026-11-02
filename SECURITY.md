# Security policy

## Supported versions

Only the latest release of plaibook and its execution environment image
gets security fixes. Run `plai update --check` to see whether you have it.

## Reporting a vulnerability

Report it privately through GitHub: open the repository's **Security**
tab and choose **Report a vulnerability**. Please do not open a public
issue or pull request for it.

Include what you found, how to reproduce it, and which version or image
digest you ran. You should get a reply within a week. A fix ships in the
next release, and the advisory is published once that release is out.

## Scope

In scope:

- The `plai` / `plaibook` CLI and `review.yml`.
- The review GitHub Actions workflows in this repository.
- The execution environment image built from this repository.
- How a review handles untrusted pull request content: prompt
  injection that changes a verdict, code from a pull request running
  outside the sandbox, or a credential reaching the sandbox.

Out of scope: vulnerabilities in a model provider's service, and
findings a review missed or got wrong, which are ordinary bugs. Report
problems in the `aknochow.*` collections to those repositories.
