# Production HTTP Security

## Scope

This document defines the production HTTP security boundary for ADT.

It covers:

- public TLS termination;
- internal HTTP service boundaries;
- CORS;
- Content Security Policy;
- HTTP security headers;
- inbound rate limiting;
- proxy trust;
- healthcheck isolation.

It does not select a production domain, certificate provider, VPS provider,
or reverse-proxy implementation.

## Public edge

Only the HTTP/TLS edge may be publicly exposed.

The backend API port and the static web server port remain internal service
ports behind the public edge.

The market-data and PAPER supervisors expose no public HTTP application ports.

TLS terminates at the public edge.

The backend and web containers may continue to use HTTP on their isolated
internal network.

Internal healthchecks must not depend on public DNS, public TLS, external
certificate issuance, or public rate limiting.

## CORS

The backend application remains responsible for its explicit CORS allowlist.

Production CORS origins must remain explicitly configured and must use
non-local HTTPS origins.

Wildcard origins must not replace the explicit production allowlist.

Credentialed cross-origin requests are not enabled by the current contract.

CORS is an HTTP access policy and does not grant authentication,
authorization, trading, capital, or operational authority.

## API security headers

The backend application continues to emit its existing API security headers.

These include the current contracts for:

- Content-Security-Policy;
- X-Content-Type-Options;
- Referrer-Policy;
- Permissions-Policy;
- X-Frame-Options;
- production HSTS;
- no-store responses for administrator routes where already defined.

The public edge may preserve or strengthen these headers but must not weaken
application security guarantees.

## Static web security headers

The static web server emits its own browser-facing security headers.

Its Content Security Policy is intentionally compatible with the current
frontend runtime model:

- application scripts are loaded from the same origin;
- inline JavaScript is not authorized;
- current React inline style attributes require inline style permission;
- API and Supabase endpoints are build-time configuration and therefore are
  not hard-coded into the static nginx configuration.

The static web server is not itself promoted to the public TLS termination
role by this contract.

## Inbound rate limiting

Production inbound client rate limiting belongs to the public HTTP/TLS edge.

The edge is the correct enforcement boundary because it:

- observes traffic before it reaches backend application workers;
- can enforce a consistent policy across backend workers or replicas;
- survives backend worker restart independently;
- owns the reviewed client-network identity boundary;
- can apply burst and sustained request controls before application work.

Phase 9-10 does not introduce an in-memory FastAPI request counter.

A process-local limiter must not be represented as a production-wide rate
limit because its state would be isolated per process and lost on restart.

No distributed rate-limit datastore is introduced by this phase.

## Client identity and forwarded headers

The backend must not independently trust arbitrary client-supplied
`X-Forwarded-For`, `X-Real-IP`, or `Forwarded` headers.

Client IP based rate limiting may be enabled only at a reviewed public edge
that owns the network trust boundary.

Any future backend use of forwarded client identity requires an explicit
trusted-proxy configuration.

## Rate-limit response semantics

A public-edge inbound rate-limit response is distinct from ADT market-data
upstream throttling.

Existing market-data `429` behavior represents an upstream public market
provider requesting backoff.

It must not be interpreted as proof that inbound ADT client rate limiting is
active.

A future public-edge limiter may return HTTP `429 Too Many Requests` according
to its reviewed deployment policy.

Exact production thresholds are not invented by this phase.

They require measured traffic and an explicit deployment decision.

## Healthchecks

Internal service healthchecks are exempt from dependency on the public edge.

Current internal health probes remain local to their service containers or
internal network.

A public rate limiter must not become a prerequisite for:

- API process liveness;
- API database readiness;
- market-supervisor process heartbeat;
- PAPER-supervisor process heartbeat;
- static web `/healthz`.

## Authority boundary

HTTP security controls are defensive infrastructure only.

They must not:

- issue START, PAUSE, RESUME, or STOP commands;
- create or renew trading leases;
- create capital authorization;
- activate PAPER sessions;
- execute PAPER cycles;
- authorize LIVE trading;
- mutate trading state as a consequence of blocking or allowing a request.

Authentication, authorization, operational authority, capital authority and
trading state remain governed by their existing contracts.

## Deferred deployment choices

Phase 9-10 does not select:

- reverse-proxy product;
- production domain;
- certificate provider;
- ACME implementation;
- inbound rate-limit numeric thresholds;
- distributed rate-limit storage;
- proxy-IP trust ranges.

Those are deployment-specific decisions and must be reviewed when the public
edge is selected.
