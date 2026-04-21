# Security posture

Northwind treats customer data as the primary asset. Nothing else in this handbook matters
if we leak a customer's warehouse manifest.

## Secrets management

Secrets live in our secrets manager. They do not live in code, in config files checked into
git, in Slack messages, in pastebins, in screenshots attached to Jira tickets, or on
sticky notes. If you need a secret to test something locally, use the development-tier
secret, which is scoped to a sandbox environment and rotates weekly.

If you discover a secret that has been committed to git, rotate it immediately. Do not
rewrite history and assume the commit is gone — treat the secret as compromised from the
moment of the push. GitHub, GitLab, and their mirrors aggressively cache commit data;
rewrites do not guarantee deletion.

## Least privilege

Every service account, human user, and automated job gets the minimum permission set
needed to do its work. This is a process, not a one-time setup — permissions accumulate
as people move between teams. We run a quarterly audit that reconciles granted permissions
against current roles, and revokes anything that is no longer needed.

## Incident response

Security incidents get the same treatment as customer-facing outages: page the on-call,
open a dedicated Slack channel, assign an incident commander, and communicate externally
once the facts are known. The incident commander is not necessarily the person who found
the issue. Often it is better if they are not, so that the discoverer can keep
investigating while the commander handles coordination.

Post-mortems are blameless. The question is "what conditions allowed this to happen", not
"who screwed up". If our process relied on one engineer catching a problem in review, that
is a process bug, not a people bug.

## See also

Engineering conventions for the code-review process. The customer-data-handling appendix.
