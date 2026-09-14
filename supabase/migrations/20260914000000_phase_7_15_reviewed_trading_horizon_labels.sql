-- ADT Phase 7-15: reviewed trading-horizon labels.
--
-- Existing profile specification schema v1 remains valid and unlabeled.
-- Horizon-aware specification schema v2 requires one exact reviewed label.
-- No horizon is inferred from timeframe, strategy, runtime behavior or history.

alter table public.operational_paper_session_profile_revisions
    add column trading_horizon text;

alter table public.operational_paper_session_profile_revisions
    drop constraint op_ps_profile_revisions_schema_version_check;

alter table public.operational_paper_session_profile_revisions
    add constraint op_ps_profile_revisions_schema_version_check
    check (schema_version in (1, 2));

alter table public.operational_paper_session_profile_revisions
    add constraint op_ps_profile_revisions_trading_horizon_shape_check
    check (
        (
            schema_version = 1
            and trading_horizon is null
        )
        or (
            schema_version = 2
            and trading_horizon is not null
            and trading_horizon in ('DAY_TRADE', 'SWING_TRADE')
        )
    );
