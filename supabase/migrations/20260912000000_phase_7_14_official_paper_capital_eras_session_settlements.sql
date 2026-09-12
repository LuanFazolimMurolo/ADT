-- ADT Phase 7-14 Gate 2B: official paper-capital designation and terminal evidence.
--
-- simulation_runs and capital_movements remain the only financial authority.
-- No posting, reservation consumption, START prevention or runtime work occurs here.
-- Gate 2C revalidates designation eligibility and mutable authority under the
-- simulation financial mutex. No parallel active-era lifecycle is introduced.

create table public.operational_paper_capital_eras (
    era_id uuid primary key default gen_random_uuid(),
    schema_version integer not null,
    designation_contract_version integer not null,
    simulation_id uuid not null,
    currency text not null,
    initial_capital numeric(20, 8) not null,
    simulation_started_at timestamptz not null,
    era_checksum text not null,
    designated_by uuid not null,
    designated_at timestamptz not null,
    designation_idempotency_key text not null,
    designation_intent_fingerprint text not null,

    constraint op_pc_era_simulation_fkey
        foreign key (simulation_id)
        references public.simulation_runs (id)
        on delete restrict,
    constraint op_pc_era_designated_by_fkey
        foreign key (designated_by)
        references auth.users (id)
        on delete restrict,
    constraint op_pc_era_simulation_key
        unique (simulation_id),
    constraint op_pc_era_identity_key
        unique (era_id, simulation_id, era_checksum),
    constraint op_pc_era_actor_idempotency_key
        unique (designated_by, designation_idempotency_key),

    constraint op_pc_era_schema_version_check
        check (schema_version = 1),
    constraint op_pc_era_contract_version_check
        check (designation_contract_version = 1),
    constraint op_pc_era_nonzero_uuids_check
        check (
            era_id <> '00000000-0000-0000-0000-000000000000'::uuid
            and simulation_id <> '00000000-0000-0000-0000-000000000000'::uuid
            and designated_by <> '00000000-0000-0000-0000-000000000000'::uuid
        ),
    constraint op_pc_era_currency_check
        check (currency ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'),
    constraint op_pc_era_initial_capital_check
        check (
            initial_capital not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and initial_capital > 0
        ),
    constraint op_pc_era_checksum_check
        check (era_checksum ~ '^[0-9a-f]{64}$'),
    constraint op_pc_era_fingerprint_check
        check (designation_intent_fingerprint ~ '^[0-9a-f]{64}$'),
    constraint op_pc_era_idempotency_key_check
        check (
            designation_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(designation_idempotency_key) <= 128
        ),
    constraint op_pc_era_timestamps_finite_check
        check (isfinite(simulation_started_at) and isfinite(designated_at)),
    constraint op_pc_era_chronology_check
        check (designated_at >= simulation_started_at)
);

comment on table public.operational_paper_capital_eras is
    'Immutable official designation of one Phase 1 simulation; capital and start time are designation evidence, not another balance or lifecycle.';

create index op_pc_era_designated_idx
    on public.operational_paper_capital_eras (designated_at desc, era_id desc);


create table public.operational_paper_session_settlements (
    settlement_id uuid primary key default gen_random_uuid(),
    schema_version integer not null,
    settlement_contract_version integer not null,
    era_id uuid not null,
    era_checksum text not null,
    simulation_id uuid not null,
    epoch_id uuid not null,
    epoch_checksum text not null,
    epoch_terminal_at timestamptz not null,
    authorization_id uuid not null,
    authorization_checksum text not null,
    session_id text not null,
    config_checksum text not null,
    state_id text not null,
    state_checksum text not null,
    dataset_version text not null,
    source_checksum text not null,
    timeline_id text not null,
    timeline_content_checksum text not null,
    initial_capital numeric(20, 8) not null,
    final_quote_cash numeric(20, 8) not null,
    realized_pnl numeric(20, 8) not null,
    unrealized_pnl numeric(20, 8) not null,
    base_quantity numeric(20, 8) not null,
    average_entry_price numeric(20, 8) not null,
    cost_basis numeric(20, 8) not null,
    total_fees numeric(20, 8) not null,
    total_slippage_cost numeric(20, 8) not null,
    settlement_delta numeric(20, 8) not null,
    ledger_movement_id uuid,
    settlement_checksum text not null,
    settled_by uuid not null,
    settled_at timestamptz not null,
    settle_idempotency_key text not null,
    settle_intent_fingerprint text not null,

    constraint op_ps_settlement_era_identity_fkey
        foreign key (era_id, simulation_id, era_checksum)
        references public.operational_paper_capital_eras (era_id, simulation_id, era_checksum)
        on delete restrict,
    constraint op_ps_settlement_simulation_fkey
        foreign key (simulation_id)
        references public.simulation_runs (id)
        on delete restrict,
    constraint op_ps_settlement_epoch_identity_fkey
        foreign key (epoch_id, epoch_checksum)
        references public.operational_paper_session_run_epochs (epoch_id, epoch_checksum)
        on delete restrict,
    constraint op_ps_settlement_authorization_fkey
        foreign key (authorization_id)
        references public.operational_paper_capital_authorizations (authorization_id)
        on delete restrict,
    constraint op_ps_settlement_movement_fkey
        foreign key (ledger_movement_id)
        references public.capital_movements (id)
        on delete restrict,
    constraint op_ps_settlement_settled_by_fkey
        foreign key (settled_by)
        references auth.users (id)
        on delete restrict,

    constraint op_ps_settlement_session_key
        unique (session_id),
    constraint op_ps_settlement_actor_idempotency_key
        unique (settled_by, settle_idempotency_key),
    constraint op_ps_settlement_movement_key
        unique (ledger_movement_id),

    constraint op_ps_settlement_schema_version_check
        check (schema_version = 1),
    constraint op_ps_settlement_contract_version_check
        check (settlement_contract_version = 1),
    constraint op_ps_settlement_nonzero_uuids_check
        check (
            settlement_id <> '00000000-0000-0000-0000-000000000000'::uuid
            and era_id <> '00000000-0000-0000-0000-000000000000'::uuid
            and simulation_id <> '00000000-0000-0000-0000-000000000000'::uuid
            and epoch_id <> '00000000-0000-0000-0000-000000000000'::uuid
            and authorization_id <> '00000000-0000-0000-0000-000000000000'::uuid
            and settled_by <> '00000000-0000-0000-0000-000000000000'::uuid
            and (
                ledger_movement_id is null
                or ledger_movement_id <> '00000000-0000-0000-0000-000000000000'::uuid
            )
        ),
    constraint op_ps_settlement_hashes_check
        check (
            era_checksum ~ '^[0-9a-f]{64}$'
            and epoch_checksum ~ '^[0-9a-f]{64}$'
            and authorization_checksum ~ '^[0-9a-f]{64}$'
            and session_id ~ '^[0-9a-f]{64}$'
            and config_checksum ~ '^[0-9a-f]{64}$'
            and state_id ~ '^[0-9a-f]{64}$'
            and state_checksum ~ '^[0-9a-f]{64}$'
            and dataset_version ~ '^[0-9a-f]{64}$'
            and source_checksum ~ '^[0-9a-f]{64}$'
            and timeline_id ~ '^[0-9a-f]{64}$'
            and timeline_content_checksum ~ '^[0-9a-f]{64}$'
            and settlement_checksum ~ '^[0-9a-f]{64}$'
            and settle_intent_fingerprint ~ '^[0-9a-f]{64}$'
        ),
    constraint op_ps_settlement_idempotency_key_check
        check (
            settle_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(settle_idempotency_key) <= 128
        ),
    constraint op_ps_settlement_amounts_finite_check
        check (
            initial_capital not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and final_quote_cash not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and realized_pnl not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and unrealized_pnl not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and base_quantity not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and average_entry_price not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and cost_basis not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and total_fees not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and total_slippage_cost not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
            and settlement_delta not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
        ),
    constraint op_ps_settlement_capital_check
        check (initial_capital > 0 and final_quote_cash >= 0),
    constraint op_ps_settlement_flat_check
        check (
            base_quantity = 0
            and average_entry_price = 0
            and cost_basis = 0
            and unrealized_pnl = 0
        ),
    constraint op_ps_settlement_audit_costs_check
        check (total_fees >= 0 and total_slippage_cost >= 0),
    constraint op_ps_settlement_arithmetic_check
        check (
            settlement_delta = final_quote_cash - initial_capital
            and settlement_delta = realized_pnl
        ),
    constraint op_ps_settlement_movement_shape_check
        check (
            (settlement_delta = 0 and ledger_movement_id is null)
            or (settlement_delta <> 0 and ledger_movement_id is not null)
        ),
    constraint op_ps_settlement_timestamps_finite_check
        check (isfinite(epoch_terminal_at) and isfinite(settled_at)),
    constraint op_ps_settlement_chronology_check
        check (settled_at >= epoch_terminal_at)
);

comment on table public.operational_paper_session_settlements is
    'Immutable terminal evidence for one deterministic paper session, linked to its official era and optional single movement in the existing capital ledger.';
comment on column public.operational_paper_session_settlements.ledger_movement_id is
    'Existing ledger link: positive delta requires TRADE_PROFIT, negative requires TRADE_LOSS, zero requires no movement. Gate 2C validates movement simulation, type and amount atomically.';
comment on column public.operational_paper_session_settlements.total_fees is
    'Audit evidence already economically included in realized PnL; never a separate settlement FEE posting.';
comment on column public.operational_paper_session_settlements.total_slippage_cost is
    'Audit evidence only; never subtracted again from settlement_delta.';

-- Config/state/timeline hashes are filesystem evidence, not database event FKs.
-- Gate 2C verifies epoch STOPPED/STOPPED, terminal time, authorization checksum,
-- simulation/session/config coherence and AUTHORIZED consumption in its transaction.
-- Gate 2D verifies canonical local evidence before that transaction. Checksums and
-- fingerprints are computed by the frozen Python domain, not recomputed in SQL.

create index op_ps_settlement_settled_idx
    on public.operational_paper_session_settlements (settled_at desc, settlement_id desc);
create index op_ps_settlement_era_history_idx
    on public.operational_paper_session_settlements (era_id, settled_at desc, settlement_id desc);
create index op_ps_settlement_simulation_idx
    on public.operational_paper_session_settlements (simulation_id);
create index op_ps_settlement_epoch_idx
    on public.operational_paper_session_settlements (epoch_id, epoch_checksum);
create index op_ps_settlement_authorization_idx
    on public.operational_paper_session_settlements (authorization_id);


create function public.protect_op_pc_era()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception using
        errcode = '55000',
        message = 'operational_paper_capital_era_immutable';
end;
$function$;

create trigger op_pc_era_protect
before update or delete on public.operational_paper_capital_eras
for each row
execute function public.protect_op_pc_era();

create function public.protect_op_ps_settlement()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception using
        errcode = '55000',
        message = 'operational_paper_session_settlement_immutable';
end;
$function$;

create trigger op_ps_settlement_protect
before update or delete on public.operational_paper_session_settlements
for each row
execute function public.protect_op_ps_settlement();

alter table public.operational_paper_capital_eras enable row level security;
alter table public.operational_paper_session_settlements enable row level security;

-- Backend-only owner connection; no Data API policies or privileges.
revoke all privileges
    on table
        public.operational_paper_capital_eras,
        public.operational_paper_session_settlements
    from public, anon, authenticated, service_role;

revoke all privileges
    on function
        public.protect_op_pc_era(),
        public.protect_op_ps_settlement()
    from public, anon, authenticated, service_role;
