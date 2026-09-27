# Phase 8 — Machine Learning and Recommendation Completion Evidence

## Status

**COMPLETE / CLOSED**

Phase 8 establishes deterministic, versioned, explainable and rigorously
evaluated machine-learning and recommendation foundations for ADT while
preserving the hard boundary between intelligence evidence and operational
execution authority.

The validated implementation baseline immediately before this documentation
closure is:

`dd118e02f73c6b6ebe0f6e8c27197178ad51a13e`

## Roadmap completion matrix

All Phase 8 roadmap deliverables are satisfied.

1. **Dataset and feature contracts with no look-ahead leakage — SATISFIED**
   - deterministic dataset and observation contracts;
   - canonical materialization;
   - causal feature engineering;
   - explicit temporal-safety and target-maturity boundaries.

2. **Model-training and evaluation pipelines — SATISFIED**
   - deterministic training contracts;
   - reproducible evaluation evidence;
   - immutable provenance and checksum bindings.

3. **ML-based regime classification — SATISFIED**
   - deterministic market-regime evidence and identity.

4. **Performance and degradation prediction — SATISFIED**
   - explicit performance/degradation evidence contracts independent from
     execution authority.

5. **Anomaly detection — SATISFIED**
   - deterministic anomaly evidence with reproducible provenance.

6. **Strategy and parameter recommendations — SATISFIED**
   - deterministic recommendation evidence;
   - recommendation identity, checksum and explanation contracts;
   - no selection or execution authority.

7. **Strategy and parameter intelligence by asset, timeframe and regime —
   SATISFIED**
   - deterministic market-context intelligence binds recommendation evidence to
     asset, timeframe and regime context.

8. **ADT Confidence Score contract, evidence model and calibrated presentation —
   SATISFIED**
   - deterministic confidence evidence;
   - confidence is evidence strength, not financial return;
   - confidence is not represented as a probability of profit.

9. **Robustness, consistency and degradation evidence independent of return —
   SATISFIED**
   - deterministic walk-forward evidence;
   - champion/challenger comparison evidence;
   - degradation and consistency evidence remain distinct from raw return.

10. **Model registry, versioning, lineage and reproducibility — SATISFIED**
    - immutable registry references;
    - deterministic model identities;
    - exact training/evaluation provenance and reproducibility bindings.

11. **Walk-forward and holdout integration with Phase 4 evidence — SATISFIED**
    - chronological walk-forward contracts;
    - holdout role separation;
    - leakage-safe fold provenance and evaluation evidence.

12. **Human approval before any recommendation becomes an active paper session —
    SATISFIED**
    - durable `PaperRecommendationApproval` evidence;
    - exact recommendation, candidate, configuration and parameter bindings;
    - human approval is versioned, auditable and revocable;
    - lifecycle is terminal `APPROVED v1 -> REVOKED v2`;
    - approval itself does not activate a session and grants no execution
      authority.

13. **Evaluate possible autonomous PAPER orchestration inside
    administrator-approved mandates after explicit safety and evaluation gates —
    SATISFIED / EVALUATED AND DEFERRED**
    - autonomous PAPER orchestration was evaluated against the Phase 7
      operational-authority model and the Phase 8 recommendation/approval
      boundaries;
    - Phase 8 deliberately does not connect recommendation approval to capital
      authorization, session materialization, activation, runner START, worker
      ownership, order creation or trading execution;
    - a recommendation approval is human decision evidence only;
    - any future autonomous PAPER orchestration requires a separate reviewed
      authority contract and explicit safety/evaluation gate.

## Delivered implementation sequence

Phase 8 was delivered incrementally through the following implementation
milestones:

- `6e19408` — ML dataset contracts;
- `51e69fe` — deterministic ML materialization;
- `f3fa09b` — feature engineering foundation;
- `b9199ca` — prediction contracts;
- `c415d3a` — temporal safety contracts;
- `97de46d` — deterministic ML training foundation;
- `753004e` — deterministic ML evaluation evidence;
- `07ed280` — deterministic ML registry lineage;
- `e268342` — deterministic market regime evidence;
- `758a779` — performance degradation prediction;
- `08031b1` — deterministic anomaly evidence;
- `0e8fb24` — deterministic strategy recommendations;
- `9bbe56a` — deterministic market context intelligence;
- `c69016e` — deterministic confidence evidence;
- `94618b2` — deterministic walk-forward evaluation;
- `de3d290` — deterministic champion/challenger evaluation;
- `caf1d08` — read-only recommendation API;
- `87cc027` — administrator recommendation explainability;
- `dd118e0` — human PAPER recommendation approval workflow.

## Recommendation and approval HTTP boundary

The recommendation HTTP surface remains read-only:

- `GET /api/v1/admin/ml/recommendations`
- `GET /api/v1/admin/ml/recommendations/{recommendation_id}`

Human approval is represented through a distinct administrative evidence
surface:

- `POST /api/v1/admin/ml/recommendations/{recommendation_id}/paper-approvals`
- `GET /api/v1/admin/ml/paper-recommendation-approvals/{approval_id}`
- `POST /api/v1/admin/ml/paper-recommendation-approvals/{approval_id}/revoke`

The approval routes do not create or mutate operational mandate authority,
capital authorization, session materialization, activation, operational run
state, worker ownership, orders or LIVE execution.

## PostgreSQL approval authority

Migration:

`20260927080000_paper_recommendation_approvals.sql`

is applied to the linked ADT Supabase project.

Remote inspection confirmed:

- `paper_recommendation_approvals` exists;
- row-level security is enabled;
- recommendation and candidate identities/checksums use exact lowercase
  SHA-256 contracts;
- approval checksum and intent fingerprint are constrained;
- approval bindings are immutable after creation;
- the only lifecycle mutation is `APPROVED v1 -> REVOKED v2`;
- revocation identity and timestamp are required for the terminal transition;
- the update trigger is installed;
- no table privileges are exposed to `anon` or `authenticated`.

Local and remote migration history are synchronized through
`20260927080000`.

Final linked migration dry-run reported:

`Remote database is up to date.`

## Integrated Phase 8 validation

The Phase 8 integrated acceptance suite passed:

- backend Phase 8 regression: **529 passed**;
- Ruff check: **PASS**;
- Ruff format: **PASS**;
- Python compileall: **PASS**;
- execution-authority sentinel: **PASS**;
- recommendation/approval HTTP route audit: **PASS**;
- local/remote Supabase migration parity: **PASS**;
- linked remote migration dry-run: **PASS**;
- `git diff --check`: **PASS**.

During integrated closure validation, one historical recommendation wiring test
was found to classify every path under
`/api/v1/admin/ml/recommendations...` as part of the read-only recommendation
resource. Phase 8-19 legitimately added the distinct
`/recommendations/{recommendation_id}/paper-approvals` child resource.

The test classifier was corrected to exclude the approval resource while
preserving the original assertion that the recommendation router itself exposes
exactly its collection and detail GET routes. The focused regression passed
**34 tests**, and the complete Phase 8 acceptance suite subsequently passed
**529 tests**.

## Safety and authority boundaries

Phase 8 does not authorize:

- automatic real-capital execution;
- LIVE order submission;
- exchange credential usage;
- implicit PAPER session activation;
- implicit operational runner START;
- automatic capital authorization;
- automatic mandate approval;
- recommendation-driven worker ownership;
- recommendation-driven order creation.

Recommendation, confidence, evaluation and approval evidence are informative
and auditable inputs. They are not execution authority.

The ADT Confidence Score remains evidence strength and must not be interpreted
as a probability of profit unless a future explicitly calibrated statistical
contract supports that interpretation.

## Autonomous PAPER orchestration decision

Possible autonomous PAPER orchestration inside administrator-approved mandates
was reviewed as required by the roadmap.

**Decision: DEFERRED.**

The existing Phase 7 operational control plane already defines separate,
explicit authorities for mandate, profile, capital authorization,
materialization, activation and operational runner control. Phase 8 does not
collapse those authorities into recommendation approval.

A future autonomous PAPER feature, if selected, must be introduced as a
separate reviewed delivery with explicit authorization composition, evaluation
thresholds, failure semantics, auditability and operational safety gates.

No such authority is granted by Phase 8.

## Closure result

Phase 8 — Machine Learning and Recommendation is **COMPLETE / CLOSED**.

All roadmap deliverables have been implemented or, for autonomous PAPER
orchestration, explicitly evaluated and deliberately deferred according to the
roadmap's safety requirement.

No automatic real-capital execution authority is introduced by this closure.
