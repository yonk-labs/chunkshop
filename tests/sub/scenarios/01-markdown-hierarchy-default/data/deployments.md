# Deployments

## Staging
Every merge to main ships to staging automatically. The pipeline runs unit
tests, integration tests, and a smoke suite before promoting the build.
Failed smoke runs block further promotion until an engineer acknowledges.

## Production
Promotion to production is manual. An on-call engineer clicks through after
verifying staging metrics for at least thirty minutes. Rollbacks are one
command and preserve the previous artifact for post-mortem review.

## Rollbacks
Use `deploy rollback` with the artifact hash from the previous run. The
command is idempotent and safe to run during an incident.
