# Production Public Official PAPER Projection

## Scope

Phase 9-13 defines a safe, bounded and read-only public projection for the
implemented ADT official PAPER runtime.

This projection must use authoritative operational PAPER control-plane evidence.

It must not reinterpret the inherited public Phase 5 paper runner as the current
official PAPER trader.

## Legacy public PAPER boundary

ADT already exposes inherited public read-only paper-session and runner
projections.

Those routes remain valid under their existing historical contract.

They are not the authoritative public projection of the Phase 7 operational
PAPER control plane because they do not expose or derive current operational
ownership, lease freshness, fencing, desired/observed state convergence, or
official PAPER availability.

Phase 9-13 does not delete, rename, or silently change the meaning of those
legacy routes.

## New public namespace

The official PAPER projection belongs under the existing public namespace.

The intended HTTP resource is:

`GET /api/v1/public/official-paper`

The endpoint is public and GET-only.

It does not require administrator authority and must not proxy an administrator
route.

## Authoritative source

The projection must derive from persisted operational PAPER control-plane
evidence.

Relevant authoritative fields include:

- operational epoch identity;
- paper session identity;
- desired state;
- observed state;
- worker claim presence;
- worker heartbeat timestamp;
- worker lease expiry;
- current fencing generation;
- persisted failure evidence;
- terminal evidence.

The HTTP request must not execute a PAPER cycle, replay trading logic, fetch
exchange data, repair state, renew a lease, or infer authority from the legacy
runner.

## Availability and freshness

The public projection must follow the Phase 9-12 availability and freshness
contract.

For actively expected execution, public health may be derived only from
authoritative evidence including:

- desired and observed operational state;
- persisted failure evidence;
- current worker ownership;
- lease freshness;
- valid heartbeat evidence;
- fencing consistency.

`lease_expires_at` remains the operational execution-ownership freshness
boundary.

A recent heartbeat alone is not sufficient if the lease is expired.

Intentional controlled inactivity must not be presented as execution failure
merely because no active lease is required.

## Process-health privacy boundary

The Phase 9-12 model also distinguishes PAPER supervisor process freshness from
operational freshness.

The initial public projection does not need to expose:

- hostnames;
- process IDs;
- container names;
- filesystem paths;
- heartbeat-file paths;
- Docker state;
- internal worker identity;
- database topology.

Internal process-health evidence may inform an eventual sanitized public health
classification only when the implementation can do so without leaking
infrastructure details.

Absence of such a public process-health field must not be replaced by invented
health evidence.

## Public response minimization

The public contract should expose only information necessary to understand the
implemented official PAPER operational state.

It must not expose:

- administrator identity;
- internal worker identity;
- database record versions used only for mutation concurrency;
- activation secrets or replay material;
- authorization internals;
- arbitrary failure diagnostics;
- filesystem locations;
- environment or secret values.

Failure information, if exposed, must use an existing bounded failure code and
must not expose arbitrary exception text.

## Bounding

The initial official PAPER public resource is a bounded snapshot.

It must not provide an unbounded operational-history scan.

If multiple current operational epochs are projected, the query must have an
explicit deterministic maximum and deterministic ordering.

The public HTTP request must not cause pagination over an unbounded repository
until exhaustion.

Future history endpoints require their own explicit bounded contract.

## No Official Portfolio claim

Phase 9-13 must not claim that the aggregate ADT Official Portfolio is already
implemented.

In particular, this phase must not invent or synthesize portfolio-wide:

- current capital;
- available cash;
- open positions;
- cumulative return;
- realized or unrealized aggregate PnL;
- total equity;
- drawdown;
- unified equity curve;
- historical portfolio operations.

Those values may be exposed only after an authoritative aggregate Official
Portfolio contract is implemented and validated.

Existing session-level evidence must not be silently summed and presented as the
Official Portfolio.

## Confidence boundary

The public official PAPER projection must not invent an ADT Confidence Score.

Any confidence value requires its separately validated evidence and presentation
contract.

A recommendation approval, ML score or model output must not be reinterpreted as
public execution health or a probability of profit.

## Read-only authority boundary

The public endpoint is observational only.

A GET request must not:

- create a PAPER session;
- start, pause, resume, or stop an operational epoch;
- activate or revoke a session;
- create or renew worker ownership;
- extend a lease;
- change a fencing token;
- create capital authorization;
- change a capital era;
- settle a session;
- execute a PAPER cycle;
- create an order;
- call an exchange for execution;
- authorize LIVE trading.

HTTP visibility never grants execution authority.

## Failure behavior

The projection must fail closed when authoritative data required for a positive
claim cannot be read or validated.

It must not convert:

- missing control-plane evidence;
- expired ownership;
- invalid timestamps;
- persisted failure;
- persistence errors;

into an apparently healthy official PAPER state.

A public read failure must remain a read failure and must not trigger recovery.

## Legacy compatibility

The existing Phase 5 public PAPER routes remain separate.

The new official resource must not silently change their schemas or semantics.

Clients that need the current official PAPER operational state must use the new
official projection once implemented.

## Phase 10 boundary

Phase 9-13 supplies a safe production public read primitive.

It does not complete the Phase 10 public product experience.

Phase 10 may later compose this projection into the ADT Live user interface
alongside separately implemented and validated product evidence.

Phase 9-13 therefore does not require:

- Official Portfolio aggregation;
- Telegram distribution;
- subscriptions or billing;
- public trading controls;
- real-capital execution;
- a Confidence Score that is not already technically validated for public use.

## Phase 9-13 decision

Phase 9-13 establishes that:

- the inherited public PAPER runner is legacy and remains unchanged;
- the Phase 7 operational control plane is the authoritative source for the new
  official PAPER projection;
- the new public resource is GET-only and read-only;
- public data is sanitized and bounded;
- operational lease expiry remains the execution freshness boundary;
- missing authoritative evidence cannot be upgraded to healthy state;
- administrator and infrastructure details remain private;
- no aggregate Official Portfolio metric is invented;
- no public request may gain trading or operational authority;
- Phase 10 product composition remains a separate delivery.
