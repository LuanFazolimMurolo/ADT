# ADT Product Vision

## Purpose and authority

This document defines what ADT is intended to become. It is a durable product
contract, not an implementation claim or a substitute for the technical
roadmap. [`ROADMAP.md`](./ROADMAP.md) defines when capabilities may be delivered,
and [`CHAT_CONTINUITY.md`](./CHAT_CONTINUITY.md) records the current handoff.

## Product identity

ADT means **Automatic Dry Trade**. It is intended to behave as a disciplined
algorithmic trader that operates continuously according to explicit strategy,
risk, data-quality and operational contracts rather than emotional decisions.

The intended experience is analogous to watching a professional trader in a
permanent public 24/7 live environment, except that the trader is the ADT
system. The primary official execution environment remains **paper trading**.
Performance must be evidenced by deterministic, auditable simulated execution;
the product must not imply that simulated results guarantee future performance.

## Product personas

### Administrator: orchestrator

The administrator's primary role is to define and supervise mandates. Depending
on the contracts implemented at each phase, a mandate may specify:

- permitted assets, markets and instruments;
- timeframes and trading horizons, such as day trade or swing trade;
- available strategies and policies;
- risk boundaries and official simulated-capital configuration;
- when an approved mandate or workflow may start, pause, resume or end.

The administrator is not expected to direct each decision with instructions
such as “buy now” or “sell now.” The intended ADT trader chooses trades inside
approved mandates using the strategy, risk and intelligence contracts that have
actually passed their delivery gates.

### Public visitor: spectator

A public visitor is analogous to a viewer watching the ADT trader. A future
read-only **ADT Live** surface may expose safe, authoritative projections such
as:

- official initial and current capital;
- cumulative return, equity curve and drawdown;
- ADT Confidence Score;
- public open positions where disclosure policy permits;
- recent and historical operations and charts;
- operational freshness and health suitable for public disclosure.

The public visitor observes the trader and cannot control it. Every projection
must remain bounded, read-only and derived from authoritative backend evidence.

### Subscriber: future commercial persona

A future commercial layer may offer subscribers authorized access to premium
ADT signals or features. This direction may require users, plans,
subscriptions, payment-provider integration, entitlement state, subscriber
lifecycle, account-to-Telegram association and authorization for premium
signals.

Pricing is deliberately not a product-vision constant. Any price discussed in
planning is illustrative only and must not become a technical contract without
a separately reviewed commercial decision. Billing and subscriptions are not
implemented by this document.

## ADT Official Portfolio

The **ADT Official Portfolio** is the canonical public paper portfolio
representing the simulated capital of the ADT trader itself. It is not the
administrator's money and not a subscriber's money. It aggregates the economic
effect of official ADT operations across the assets and mandates admitted to
the official portfolio.

When implemented, its authoritative contract should support:

- initial simulated capital and available cash;
- open positions;
- realized and unrealized performance where authoritative evidence exists;
- total equity and cumulative performance;
- drawdown and a unified equity curve;
- historical operations.

The public capital is primarily evidence of ADT performance. For example, an
initial paper capital of `100,000 USDT`, current capital of `137,420 USDT` and
return of `+37.42%` means that the official simulated trader produced that
track record. It does not represent customer assets or a promise of return.

### Capital eras and resets

An administrator may later restart or change the official simulated capital.
Such a reset must never rewrite or destroy prior performance. The product must
model resets as immutable, versioned portfolio eras, or an equivalent auditable
contract.

For example, Era 1 may begin at `10,000` and end at `18,700`, while Era 2 begins
at `1,000,000`. Both eras remain independently inspectable, with explicit
boundaries and preserved history.

The Official Portfolio and capital-era model are future product contracts. They
must not be described as implemented until their technical delivery and gates
are complete.

## ADT Confidence Score

The **ADT Confidence Score** expresses the strength of evidence that current ADT
behavior is robust, consistent and adapting adequately rather than merely
benefiting from luck. It is distinct from financial return:

- performance asks, “How much did the ADT trader gain or lose?”;
- confidence asks, “How strong is the evidence supporting the current
  behavior?”

Confidence may rise or fall independently of portfolio return. A future
validated model may consider:

- sample size, number of trades and observation length;
- walk-forward and holdout evidence;
- strategy stability and behavior across regimes;
- degradation and drawdown behavior;
- recent versus expected performance;
- consistency across relevant assets and timeframes;
- anomaly and data-quality state;
- model calibration where applicable.

Preferred product language is, for example, `ADT Confidence: 87/100`. This is
not a guaranteed probability of profit. In particular, `87/100` must not be
presented as an `87%` probability of winning unless a future explicitly
calibrated statistical contract supports that exact interpretation.

The Confidence Score does not exist merely because it is defined here. Phase 8
must specify, evaluate and validate its evidence and presentation.

## Telegram and assisted distribution

The public product is intended eventually to expose an official Telegram entry
point or link. Future authorized Telegram delivery may include:

- entry, exit, stop, invalidation and expiry signals;
- observed price, timeframe, strategy and context;
- risk context and percentage-allocation guidance;
- signal-specific confidence or evidence where authoritative;
- acknowledgements and operational or error alerts.

Telegram initially supports **human manual execution**. A subscriber may choose
to reproduce an ADT operation with independently controlled personal capital.
ADT must not assume that subscriber capital equals the Official Portfolio.
Allocation percentages or risk context may help a human interpret a signal,
but do not grant ADT authority over that person's funds.

Telegram delivery, premium entitlements and billing remain future scope until
their roadmap deliveries are implemented and validated.

## Real-capital boundary

The initial product does not automatically operate administrator or subscriber
real money. Exchange-account execution is a separate optional future
assessment, not a consequence of completing paper trading, Telegram or
subscriptions.

Any automated real-capital phase requires explicit authorization and new
security, custody, reconciliation, legal/regulatory, loss-control, incident and
operational evidence. The project may permanently remain a paper trader with
manual signal-assisted execution.

## Intelligence direction

Future intelligence and machine learning should help ADT understand performance
by asset, timeframe, strategy, parameters, regime and market context. Validated
capabilities may include:

- regime classification;
- degradation prediction;
- strategy and parameter recommendations;
- anomaly detection;
- confidence evidence and calibration.

The long-term paper objective may include autonomous strategy or session
decisions inside administrator-approved mandates and strict risk bounds. Human
approval requirements may evolve for paper operation only after explicit
safety and evaluation review. Nothing in this direction implies automatic
real-money execution.

## Architectural continuity

This direction extends rather than invalidates completed foundations:

- Phase 5 remains the deterministic trading, risk and paper-engine foundation;
- Phase 6 remains the read-only visualization, charting and frontend
  foundation;
- Phase 7 adds operational orchestration and control-plane foundations;
- later phases may add intelligence, production delivery and distribution only
  through their own reviewed contracts and gates.

Existing deterministic, resumable, idempotent and auditable boundaries remain
requirements for every extension.
