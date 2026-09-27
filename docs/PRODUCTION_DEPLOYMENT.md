# ADT Production Deployment Contract

## Purpose

Production deployment is a separately authorized operational action.

A successful CI run, commit, recommendation, PAPER approval or container build
does not itself authorize deployment.

## Release identity

A production release is identified by one exact Git commit SHA.

Mutable aliases such as `latest` must not be treated as release identity.

Future registry images must be bound to the exact release commit, preferably by:

- immutable commit-SHA tag;
- immutable registry digest.

The deployment target must never silently substitute a different commit or
mutable image tag.

## Protected environment

Production release preparation uses the GitHub environment:

`production`

Repository configuration should require human approval for that environment
before any future operational deployment job can receive production credentials.

The workflow file alone cannot create or enforce GitHub environment protection
rules. Those rules are repository-side configuration.

## Current Phase 9-06 boundary

The current workflow is intentionally release-preparation only.

It may:

- require manual `workflow_dispatch`;
- require an explicit full commit SHA;
- checkout that exact commit;
- validate production contracts;
- build the production backend image;
- build the production frontend image with non-secret public placeholders;
- generate release metadata;
- upload release metadata as a CI artifact.

It must not:

- push an image to a container registry;
- connect to a production host;
- use SSH, SCP or rsync;
- run `docker compose up`, `down`, `restart` or `pull` on a host;
- apply Supabase migrations;
- mutate production PostgreSQL;
- create or restore production backups;
- start, stop or activate PAPER trading;
- grant capital authority;
- configure exchange credentials;
- perform LIVE trading actions.

## Registry decision

No production container registry is selected yet.

The existing production Compose file therefore retains its current local image
names until a registry contract is reviewed.

A later delivery may select GHCR or another registry and update the Compose
contract to immutable remote image references.

## Host decision

The topology is a single production host plus managed Supabase/PostgreSQL, but
the concrete hosting provider and host identity are not selected here.

No deployment host address, username, SSH key or host fingerprint belongs in
the repository.

When host deployment is introduced, credentials must live only in the protected
production environment or an equivalent reviewed secret-management mechanism.

## Database migrations

Remote migration application is a separately authorized operational action.

The deployment workflow must not run:

`supabase db push`

or any equivalent schema mutation automatically.

Migration review, dry-run, target verification and explicit authorization remain
separate gates.

## PAPER and execution authority

Deployment establishes software availability only.

It does not authorize:

- PAPER activation;
- recommendation-driven activation;
- capital allocation;
- order creation;
- LIVE execution.

Existing application authorization boundaries remain mandatory.

## Failure behavior

Release preparation must fail closed when:

- the requested Git reference is not a full commit SHA;
- checkout does not resolve to the requested SHA;
- production contracts fail validation;
- container images fail to build.

No partial failure may fall through into an operational deployment action.

## Future operational deployment

Actual deployment remains blocked until at least these contracts exist:

1. selected container registry;
2. immutable image publication;
3. selected production host;
4. GitHub `production` environment protections;
5. production secret provisioning;
6. reviewed host bootstrap;
7. backup/restore readiness;
8. rollback procedure;
9. explicit deployment authorization.

This document does not authorize those actions.
