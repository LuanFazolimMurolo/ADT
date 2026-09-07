-- ADT Phase 7-11: backend-only operational paper runner-control authority.
--
-- A run epoch is one durable administrative execution interval for one exact
-- activated paper session. ACTIVATED does not mean RUNNING.
--
-- PostgreSQL owns operational run-control authority and worker fencing.
-- The filesystem remains authoritative for immutable PaperSessionConfig and
-- paper execution artifacts.

create table public.operational_paper_session_run_epochs (
    epoch_id uuid primary key default gen_random_uuid(),

    schema_version integer not null,
    run_contract_version integer not null,

    desired_state text not null,
    observed_state text not null,

    record_version bigint not null,
    fencing_token bigint not null,

    activation_id uuid not null,
    activation_checksum text not null,

    materialization_id uuid not null,
    materialization_checksum text not null,

    authorization_id uuid not null,
    authorization_checksum text not null,

    profile_id uuid not null,
    profile_approved_revision bigint not null,
    profile_specification_checksum text not null,

    mandate_id uuid not null,
    mandate_approved_revision bigint not null,
    mandate_specification_checksum text not null,

    simulation_id uuid not null,

    session_id text not null,
    config_checksum text not null,
    epoch_checksum text not null,

    start_requested_by uuid not null,
    start_requested_at timestamptz not null,
    start_idempotency_key text not null,
    start_intent_fingerprint text not null,

    worker_id uuid,
    worker_claimed_at timestamptz,
    worker_heartbeat_at timestamptz,
    worker_lease_expires_at timestamptz,

    failure_code text,
    failure_at timestamptz,

    terminal_at timestamptz,

    constraint op_ps_run_epoch_activation_fkey
        foreign key (activation_id)
        references public.operational_paper_session_activations (activation_id)
        on delete restrict,

    constraint op_ps_run_epoch_materialization_fkey
        foreign key (materialization_id)
        references public.operational_paper_session_materializations (materialization_id)
        on delete restrict,

    constraint op_ps_run_epoch_authorization_fkey
        foreign key (authorization_id)
        references public.operational_paper_capital_authorizations (authorization_id)
        on delete restrict,

    constraint op_ps_run_epoch_profile_revision_fkey
        foreign key (
            profile_id,
            profile_approved_revision,
            profile_specification_checksum
        )
        references public.operational_paper_session_profile_revisions (
            profile_id,
            revision,
            specification_checksum
        )
        on delete restrict,

    constraint op_ps_run_epoch_mandate_revision_fkey
        foreign key (
            mandate_id,
            mandate_approved_revision,
            mandate_specification_checksum
        )
        references public.operational_mandate_revisions (
            mandate_id,
            revision,
            specification_checksum
        )
        on delete restrict,

    constraint op_ps_run_epoch_simulation_fkey
        foreign key (simulation_id)
        references public.simulation_runs (id)
        on delete restrict,

    constraint op_ps_run_epoch_start_requested_by_fkey
        foreign key (start_requested_by)
        references auth.users (id)
        on delete restrict,

    constraint op_ps_run_epoch_identity_key
        unique (epoch_id, epoch_checksum),

    constraint op_ps_run_epoch_actor_start_idempotency_key
        unique (start_requested_by, start_idempotency_key),

    constraint op_ps_run_epoch_schema_version_check
        check (schema_version = 1),

    constraint op_ps_run_epoch_contract_version_check
        check (run_contract_version = 1),

    constraint op_ps_run_epoch_desired_state_check
        check (desired_state in ('RUNNING', 'PAUSED', 'STOPPED')),

    constraint op_ps_run_epoch_observed_state_check
        check (
            observed_state in (
                'PENDING',
                'STARTING',
                'RUNNING',
                'PAUSED',
                'RECOVERING',
                'STOPPING',
                'STOPPED',
                'FAILED'
            )
        ),

    constraint op_ps_run_epoch_record_version_check
        check (record_version >= 1),

    constraint op_ps_run_epoch_fencing_token_check
        check (fencing_token >= 0),

    constraint op_ps_run_epoch_profile_revision_check
        check (profile_approved_revision >= 1),

    constraint op_ps_run_epoch_mandate_revision_check
        check (mandate_approved_revision >= 1),

    constraint op_ps_run_epoch_activation_checksum_check
        check (activation_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_epoch_materialization_checksum_check
        check (materialization_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_epoch_authorization_checksum_check
        check (authorization_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_epoch_profile_checksum_check
        check (profile_specification_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_epoch_mandate_checksum_check
        check (mandate_specification_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_epoch_session_id_check
        check (session_id ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_epoch_config_checksum_check
        check (config_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_epoch_checksum_check
        check (epoch_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_epoch_start_fingerprint_check
        check (start_intent_fingerprint ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_epoch_start_idempotency_key_check
        check (
            start_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(start_idempotency_key) <= 128
        ),

    constraint op_ps_run_epoch_failure_code_check
        check (
            failure_code is null
            or failure_code in (
                'AUTHORITY_LOST',
                'ACTIVATION_REVOKED',
                'CONFIG_UNAVAILABLE',
                'CONFIG_IDENTITY_CONFLICT',
                'PLUGIN_UNAVAILABLE',
                'RAW_NOT_READY',
                'LOCAL_RUNNER_BUSY',
                'LOCAL_STATE_INVALID',
                'LEASE_LOST',
                'INTERNAL_ERROR'
            )
        ),

    constraint op_ps_run_epoch_nonzero_epoch_id_check
        check (
            epoch_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_epoch_nonzero_activation_id_check
        check (
            activation_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_epoch_nonzero_materialization_id_check
        check (
            materialization_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_epoch_nonzero_authorization_id_check
        check (
            authorization_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_epoch_nonzero_profile_id_check
        check (
            profile_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_epoch_nonzero_mandate_id_check
        check (
            mandate_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_epoch_nonzero_simulation_id_check
        check (
            simulation_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_epoch_nonzero_start_actor_check
        check (
            start_requested_by <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_epoch_nonzero_worker_id_check
        check (
            worker_id is null
            or worker_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_epoch_start_requested_at_finite_check
        check (isfinite(start_requested_at)),

    constraint op_ps_run_epoch_worker_collective_check
        check (
            (
                worker_id is null
                and worker_claimed_at is null
                and worker_heartbeat_at is null
                and worker_lease_expires_at is null
            )
            or
            (
                worker_id is not null
                and worker_claimed_at is not null
                and worker_heartbeat_at is not null
                and worker_lease_expires_at is not null
            )
        ),

    constraint op_ps_run_epoch_worker_finite_check
        check (
            (worker_claimed_at is null or isfinite(worker_claimed_at))
            and (worker_heartbeat_at is null or isfinite(worker_heartbeat_at))
            and (
                worker_lease_expires_at is null
                or isfinite(worker_lease_expires_at)
            )
        ),

    constraint op_ps_run_epoch_worker_chronology_check
        check (
            worker_id is null
            or (
                worker_claimed_at <= worker_heartbeat_at
                and worker_heartbeat_at < worker_lease_expires_at
            )
        ),

    constraint op_ps_run_epoch_worker_fence_shape_check
        check (
            worker_id is null
            or fencing_token >= 1
        ),

    constraint op_ps_run_epoch_worker_state_shape_check
        check (
            (
                observed_state in (
                    'STARTING',
                    'RUNNING',
                    'RECOVERING',
                    'STOPPING'
                )
                and worker_id is not null
            )
            or
            (
                observed_state in (
                    'PENDING',
                    'PAUSED',
                    'STOPPED',
                    'FAILED'
                )
                and worker_id is null
            )
        ),

    constraint op_ps_run_epoch_failure_collective_check
        check (
            (failure_code is null and failure_at is null)
            or
            (failure_code is not null and failure_at is not null)
        ),

    constraint op_ps_run_epoch_failure_at_finite_check
        check (failure_at is null or isfinite(failure_at)),

    constraint op_ps_run_epoch_terminal_at_finite_check
        check (terminal_at is null or isfinite(terminal_at)),

    constraint op_ps_run_epoch_terminal_shape_check
        check (
            (
                observed_state = 'STOPPED'
                and desired_state = 'STOPPED'
                and failure_code is null
                and failure_at is null
                and terminal_at is not null
            )
            or
            (
                observed_state = 'FAILED'
                and failure_code is not null
                and failure_at is not null
                and terminal_at is not null
            )
            or
            (
                observed_state not in ('STOPPED', 'FAILED')
                and failure_code is null
                and failure_at is null
                and terminal_at is null
            )
        ),

    constraint op_ps_run_epoch_stopping_shape_check
        check (
            observed_state <> 'STOPPING'
            or desired_state = 'STOPPED'
        ),

    constraint op_ps_run_epoch_terminal_chronology_check
        check (
            terminal_at is null
            or terminal_at >= start_requested_at
        ),

    constraint op_ps_run_epoch_failure_chronology_check
        check (
            failure_at is null
            or failure_at >= start_requested_at
        )
);

comment on table public.operational_paper_session_run_epochs is
    'Durable historical run-control epochs, desired/observed state, worker lease and fencing authority for exact activated paper sessions.';

create unique index op_ps_run_epoch_one_current_per_session_uidx
    on public.operational_paper_session_run_epochs (session_id)
    where observed_state not in ('STOPPED', 'FAILED');

create index op_ps_run_epoch_session_history_idx
    on public.operational_paper_session_run_epochs (
        session_id,
        start_requested_at desc,
        epoch_id desc
    );

create index op_ps_run_epoch_activation_history_idx
    on public.operational_paper_session_run_epochs (
        activation_id,
        start_requested_at desc,
        epoch_id desc
    );

create index op_ps_run_epoch_worker_lease_idx
    on public.operational_paper_session_run_epochs (
        worker_lease_expires_at,
        epoch_id
    )
    where worker_id is not null;

create index op_ps_run_epoch_list_idx
    on public.operational_paper_session_run_epochs (
        start_requested_at desc,
        epoch_id desc
    );


create table public.operational_paper_session_run_commands (
    command_id uuid primary key default gen_random_uuid(),

    command_contract_version integer not null,

    epoch_id uuid not null,
    epoch_checksum text not null,

    command_type text not null,
    desired_state text not null,

    expected_record_version bigint not null,
    resulting_record_version bigint not null,

    actor_id uuid not null,
    requested_at timestamptz not null,

    idempotency_key text not null,
    intent_fingerprint text not null,

    constraint op_ps_run_command_epoch_identity_fkey
        foreign key (epoch_id, epoch_checksum)
        references public.operational_paper_session_run_epochs (
            epoch_id,
            epoch_checksum
        )
        on delete restrict,

    constraint op_ps_run_command_actor_fkey
        foreign key (actor_id)
        references auth.users (id)
        on delete restrict,

    constraint op_ps_run_command_actor_idempotency_key
        unique (actor_id, idempotency_key),

    constraint op_ps_run_command_epoch_result_version_key
        unique (epoch_id, resulting_record_version),

    constraint op_ps_run_command_contract_version_check
        check (command_contract_version = 1),

    constraint op_ps_run_command_type_check
        check (command_type in ('START', 'PAUSE', 'RESUME', 'STOP')),

    constraint op_ps_run_command_desired_state_check
        check (desired_state in ('RUNNING', 'PAUSED', 'STOPPED')),

    constraint op_ps_run_command_target_check
        check (
            (
                command_type in ('START', 'RESUME')
                and desired_state = 'RUNNING'
            )
            or (
                command_type = 'PAUSE'
                and desired_state = 'PAUSED'
            )
            or (
                command_type = 'STOP'
                and desired_state = 'STOPPED'
            )
        ),

    constraint op_ps_run_command_version_check
        check (
            expected_record_version >= 0
            and resulting_record_version >= 1
            and resulting_record_version = expected_record_version + 1
        ),

    constraint op_ps_run_command_epoch_checksum_check
        check (epoch_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_command_fingerprint_check
        check (intent_fingerprint ~ '^[0-9a-f]{64}$'),

    constraint op_ps_run_command_idempotency_key_check
        check (
            idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(idempotency_key) <= 128
        ),

    constraint op_ps_run_command_nonzero_command_id_check
        check (
            command_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_command_nonzero_epoch_id_check
        check (
            epoch_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_command_nonzero_actor_id_check
        check (
            actor_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_ps_run_command_requested_at_finite_check
        check (isfinite(requested_at))
);

comment on table public.operational_paper_session_run_commands is
    'Append-only administrative START, PAUSE, RESUME and STOP intent history for operational paper run epochs.';

create index op_ps_run_command_epoch_history_idx
    on public.operational_paper_session_run_commands (
        epoch_id,
        resulting_record_version,
        command_id
    );

create index op_ps_run_command_list_idx
    on public.operational_paper_session_run_commands (
        requested_at desc,
        command_id desc
    );


alter table public.operational_paper_session_run_epochs
    enable row level security;

alter table public.operational_paper_session_run_commands
    enable row level security;

-- No RLS policies are created. Runner-control authority is backend-only
-- through the direct PostgreSQL owner connection.

revoke all privileges
    on table
        public.operational_paper_session_run_epochs,
        public.operational_paper_session_run_commands
    from public, anon, authenticated, service_role;

-- B1A-END


create function public.op_ps_run_transition_allowed(
    old_state text,
    new_state text
)
returns boolean
language sql
immutable
strict
set search_path = ''
as $function$
    select case old_state
        when 'PENDING' then
            new_state in ('STARTING', 'PAUSED', 'STOPPED', 'FAILED')
        when 'STARTING' then
            new_state in (
                'RUNNING',
                'PAUSED',
                'RECOVERING',
                'STOPPING',
                'FAILED'
            )
        when 'RUNNING' then
            new_state in (
                'PAUSED',
                'RECOVERING',
                'STOPPING',
                'FAILED'
            )
        when 'PAUSED' then
            new_state in ('STARTING', 'STOPPED', 'FAILED')
        when 'RECOVERING' then
            new_state in (
                'STARTING',
                'PAUSED',
                'STOPPING',
                'STOPPED',
                'FAILED'
            )
        when 'STOPPING' then
            new_state in ('RECOVERING', 'STOPPED', 'FAILED')
        when 'STOPPED' then false
        when 'FAILED' then false
        else false
    end;
$function$;

comment on function public.op_ps_run_transition_allowed(text, text) is
    'Closed observed-state transition graph for operational paper run epochs.';


create function public.validate_op_ps_run_epoch_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    simulation_status text;
    simulation_currency text;

    authorization_state text;
    stored_authorization_checksum text;
    authorization_profile_id uuid;
    authorization_profile_revision bigint;
    authorization_profile_checksum text;
    authorization_simulation_id uuid;
    authorization_quote_asset text;
    authorization_capital numeric;
    authorization_created_at timestamptz;

    profile_state text;
    profile_current_revision bigint;
    profile_approved_revision bigint;
    profile_approved_checksum text;
    profile_approved_at timestamptz;

    revision_mandate_id uuid;
    revision_mandate_revision bigint;
    revision_mandate_checksum text;
    revision_quote_asset text;

    mandate_state text;
    mandate_current_revision bigint;
    mandate_approved_revision bigint;
    mandate_approved_checksum text;
    mandate_approved_at timestamptz;

    materialization_state text;
    stored_materialization_checksum text;
    materialization_authorization_id uuid;
    materialization_authorization_checksum text;
    materialization_profile_id uuid;
    materialization_profile_revision bigint;
    materialization_profile_checksum text;
    materialization_mandate_id uuid;
    materialization_mandate_revision bigint;
    materialization_mandate_checksum text;
    materialization_simulation_id uuid;
    materialization_session_id text;
    materialization_config_checksum text;
    materialized_at timestamptz;

    activation_state text;
    stored_activation_checksum text;
    activation_materialization_id uuid;
    activation_materialization_checksum text;
    activation_authorization_id uuid;
    activation_authorization_checksum text;
    activation_profile_id uuid;
    activation_profile_revision bigint;
    activation_profile_checksum text;
    activation_mandate_id uuid;
    activation_mandate_revision bigint;
    activation_mandate_checksum text;
    activation_simulation_id uuid;
    activation_session_id text;
    activation_config_checksum text;
    activation_authorized_at timestamptz;
begin
    if new.desired_state <> 'RUNNING'
        or new.observed_state <> 'PENDING'
        or new.record_version <> 1
        or new.fencing_token <> 0
        or new.worker_id is not null
        or new.worker_claimed_at is not null
        or new.worker_heartbeat_at is not null
        or new.worker_lease_expires_at is not null
        or new.failure_code is not null
        or new.failure_at is not null
        or new.terminal_at is not null
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_epoch_initial_state_invalid';
    end if;

    -- Gate 1 canonical lock order begins at the Phase 1 simulation mutex.
    select simulation.status, simulation.currency
    into simulation_status, simulation_currency
    from public.simulation_runs as simulation
    where simulation.id = new.simulation_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_run_simulation_missing';
    end if;

    if simulation_status <> 'ACTIVE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_simulation_not_active';
    end if;

    -- Capital capital_authorization.
    select
        capital_authorization.state,
        capital_authorization.authorization_checksum,
        capital_authorization.profile_id,
        capital_authorization.profile_approved_revision,
        capital_authorization.profile_specification_checksum,
        capital_authorization.simulation_id,
        capital_authorization.quote_asset,
        capital_authorization.authorized_capital,
        capital_authorization.created_at
    into
        authorization_state,
        stored_authorization_checksum,
        authorization_profile_id,
        authorization_profile_revision,
        authorization_profile_checksum,
        authorization_simulation_id,
        authorization_quote_asset,
        authorization_capital,
        authorization_created_at
    from public.operational_paper_capital_authorizations as capital_authorization
    where capital_authorization.authorization_id = new.authorization_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_run_authorization_missing';
    end if;

    if authorization_state <> 'AUTHORIZED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_authorization_not_authorized';
    end if;

    if stored_authorization_checksum is distinct from new.authorization_checksum
        or authorization_profile_id is distinct from new.profile_id
        or authorization_profile_revision is distinct from new.profile_approved_revision
        or authorization_profile_checksum is distinct from new.profile_specification_checksum
        or authorization_simulation_id is distinct from new.simulation_id
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_authorization_binding_mismatch';
    end if;

    if authorization_capital is null
        or authorization_capital <= 0
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_authorized_capital_invalid';
    end if;

    -- Approved profile aggregate.
    select
        profile.state,
        profile.current_revision,
        profile.approved_revision,
        profile.approved_checksum,
        profile.approved_at
    into
        profile_state,
        profile_current_revision,
        profile_approved_revision,
        profile_approved_checksum,
        profile_approved_at
    from public.operational_paper_session_profiles as profile
    where profile.profile_id = new.profile_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_run_profile_missing';
    end if;

    if profile_state <> 'APPROVED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_profile_not_approved';
    end if;

    if profile_current_revision is distinct from new.profile_approved_revision
        or profile_approved_revision is distinct from new.profile_approved_revision
        or profile_approved_checksum is distinct from new.profile_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_profile_binding_mismatch';
    end if;

    select
        revision.mandate_id,
        revision.mandate_approved_revision,
        revision.mandate_specification_checksum,
        revision.quote_asset
    into
        revision_mandate_id,
        revision_mandate_revision,
        revision_mandate_checksum,
        revision_quote_asset
    from public.operational_paper_session_profile_revisions as revision
    where revision.profile_id = new.profile_id
      and revision.revision = new.profile_approved_revision
      and revision.specification_checksum = new.profile_specification_checksum;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_run_profile_revision_missing';
    end if;

    if revision_mandate_id is distinct from new.mandate_id
        or revision_mandate_revision is distinct from new.mandate_approved_revision
        or revision_mandate_checksum is distinct from new.mandate_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_profile_mandate_binding_mismatch';
    end if;

    if authorization_quote_asset is distinct from revision_quote_asset
        or simulation_currency is distinct from revision_quote_asset
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_quote_asset_binding_mismatch';
    end if;

    -- Approved mandate aggregate.
    select
        mandate.state,
        mandate.current_revision,
        mandate.approved_revision,
        mandate.approved_checksum,
        mandate.approved_at
    into
        mandate_state,
        mandate_current_revision,
        mandate_approved_revision,
        mandate_approved_checksum,
        mandate_approved_at
    from public.operational_mandates as mandate
    where mandate.mandate_id = new.mandate_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_run_mandate_missing';
    end if;

    if mandate_state <> 'APPROVED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_mandate_not_approved';
    end if;

    if mandate_current_revision is distinct from new.mandate_approved_revision
        or mandate_approved_revision is distinct from new.mandate_approved_revision
        or mandate_approved_checksum is distinct from new.mandate_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_mandate_binding_mismatch';
    end if;

    -- Exact MATERIALIZED local-config provenance.
    select
        materialization.state,
        materialization.materialization_checksum,
        materialization.authorization_id,
        materialization.authorization_checksum,
        materialization.profile_id,
        materialization.profile_approved_revision,
        materialization.profile_specification_checksum,
        materialization.mandate_id,
        materialization.mandate_approved_revision,
        materialization.mandate_specification_checksum,
        materialization.simulation_id,
        materialization.session_id,
        materialization.config_checksum,
        materialization.materialized_at
    into
        materialization_state,
        stored_materialization_checksum,
        materialization_authorization_id,
        materialization_authorization_checksum,
        materialization_profile_id,
        materialization_profile_revision,
        materialization_profile_checksum,
        materialization_mandate_id,
        materialization_mandate_revision,
        materialization_mandate_checksum,
        materialization_simulation_id,
        materialization_session_id,
        materialization_config_checksum,
        materialized_at
    from public.operational_paper_session_materializations as materialization
    where materialization.materialization_id = new.materialization_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_run_materialization_missing';
    end if;

    if materialization_state <> 'MATERIALIZED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_materialization_not_materialized';
    end if;

    if stored_materialization_checksum is distinct from new.materialization_checksum
        or materialization_authorization_id is distinct from new.authorization_id
        or materialization_authorization_checksum is distinct from new.authorization_checksum
        or materialization_profile_id is distinct from new.profile_id
        or materialization_profile_revision is distinct from new.profile_approved_revision
        or materialization_profile_checksum is distinct from new.profile_specification_checksum
        or materialization_mandate_id is distinct from new.mandate_id
        or materialization_mandate_revision is distinct from new.mandate_approved_revision
        or materialization_mandate_checksum is distinct from new.mandate_specification_checksum
        or materialization_simulation_id is distinct from new.simulation_id
        or materialization_session_id is distinct from new.session_id
        or materialization_config_checksum is distinct from new.config_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_materialization_binding_mismatch';
    end if;

    -- Activation is locked last among upstream authority rows.
    select
        activation.state,
        activation.activation_checksum,
        activation.materialization_id,
        activation.materialization_checksum,
        activation.authorization_id,
        activation.authorization_checksum,
        activation.profile_id,
        activation.profile_approved_revision,
        activation.profile_specification_checksum,
        activation.mandate_id,
        activation.mandate_approved_revision,
        activation.mandate_specification_checksum,
        activation.simulation_id,
        activation.session_id,
        activation.config_checksum,
        activation.authorized_at
    into
        activation_state,
        stored_activation_checksum,
        activation_materialization_id,
        activation_materialization_checksum,
        activation_authorization_id,
        activation_authorization_checksum,
        activation_profile_id,
        activation_profile_revision,
        activation_profile_checksum,
        activation_mandate_id,
        activation_mandate_revision,
        activation_mandate_checksum,
        activation_simulation_id,
        activation_session_id,
        activation_config_checksum,
        activation_authorized_at
    from public.operational_paper_session_activations as activation
    where activation.activation_id = new.activation_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_run_activation_missing';
    end if;

    if activation_state <> 'AUTHORIZED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_activation_not_authorized';
    end if;

    if stored_activation_checksum is distinct from new.activation_checksum
        or activation_materialization_id is distinct from new.materialization_id
        or activation_materialization_checksum is distinct from new.materialization_checksum
        or activation_authorization_id is distinct from new.authorization_id
        or activation_authorization_checksum is distinct from new.authorization_checksum
        or activation_profile_id is distinct from new.profile_id
        or activation_profile_revision is distinct from new.profile_approved_revision
        or activation_profile_checksum is distinct from new.profile_specification_checksum
        or activation_mandate_id is distinct from new.mandate_id
        or activation_mandate_revision is distinct from new.mandate_approved_revision
        or activation_mandate_checksum is distinct from new.mandate_specification_checksum
        or activation_simulation_id is distinct from new.simulation_id
        or activation_session_id is distinct from new.session_id
        or activation_config_checksum is distinct from new.config_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_activation_binding_mismatch';
    end if;

    if materialized_at is null
        or profile_approved_at is null
        or mandate_approved_at is null
        or new.start_requested_at < authorization_created_at
        or new.start_requested_at < profile_approved_at
        or new.start_requested_at < mandate_approved_at
        or new.start_requested_at < materialized_at
        or new.start_requested_at < activation_authorized_at
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_start_requested_at_invalid';
    end if;

    return new;
end;
$function$;

comment on function public.validate_op_ps_run_epoch_insert() is
    'Linearizes START persistence against current ACTIVE simulation, AUTHORIZED capital and activation, APPROVED profile/mandate and exact MATERIALIZED session provenance.';

create trigger op_ps_run_epoch_validate_insert
before insert on public.operational_paper_session_run_epochs
for each row
execute function public.validate_op_ps_run_epoch_insert();

revoke all privileges
    on function
        public.op_ps_run_transition_allowed(text, text),
        public.validate_op_ps_run_epoch_insert()
    from public, anon, authenticated, service_role;

-- B1B1-END


create function public.protect_op_ps_run_epoch()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    administrative_command_type text;
    expected_command_type text;
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_delete_forbidden';
    end if;

    -- Historical identity and upstream provenance are immutable.
    if new.epoch_id is distinct from old.epoch_id
        or new.schema_version is distinct from old.schema_version
        or new.run_contract_version is distinct from old.run_contract_version

        or new.activation_id is distinct from old.activation_id
        or new.activation_checksum is distinct from old.activation_checksum

        or new.materialization_id is distinct from old.materialization_id
        or new.materialization_checksum is distinct from old.materialization_checksum

        or new.authorization_id is distinct from old.authorization_id
        or new.authorization_checksum is distinct from old.authorization_checksum

        or new.profile_id is distinct from old.profile_id
        or new.profile_approved_revision is distinct from old.profile_approved_revision
        or new.profile_specification_checksum is distinct from old.profile_specification_checksum

        or new.mandate_id is distinct from old.mandate_id
        or new.mandate_approved_revision is distinct from old.mandate_approved_revision
        or new.mandate_specification_checksum is distinct from old.mandate_specification_checksum

        or new.simulation_id is distinct from old.simulation_id

        or new.session_id is distinct from old.session_id
        or new.config_checksum is distinct from old.config_checksum
        or new.epoch_checksum is distinct from old.epoch_checksum

        or new.start_requested_by is distinct from old.start_requested_by
        or new.start_requested_at is distinct from old.start_requested_at
        or new.start_idempotency_key is distinct from old.start_idempotency_key
        or new.start_intent_fingerprint is distinct from old.start_intent_fingerprint
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_immutable_fields_changed';
    end if;

    if new.record_version is distinct from old.record_version + 1 then
        raise exception using
            errcode = '40001',
            message = 'operational_paper_session_run_epoch_record_version_conflict';
    end if;

    if old.observed_state in ('STOPPED', 'FAILED') then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_terminal';
    end if;

    if new.fencing_token < old.fencing_token
        or new.fencing_token > old.fencing_token + 1
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_fencing_token_invalid';
    end if;

    -- Desired-state changes are administrative command mutations only.
    if new.desired_state is distinct from old.desired_state then
        if new.observed_state is distinct from old.observed_state
            or new.fencing_token is distinct from old.fencing_token

            or new.worker_id is distinct from old.worker_id
            or new.worker_claimed_at is distinct from old.worker_claimed_at
            or new.worker_heartbeat_at is distinct from old.worker_heartbeat_at
            or new.worker_lease_expires_at is distinct from old.worker_lease_expires_at

            or new.failure_code is distinct from old.failure_code
            or new.failure_at is distinct from old.failure_at
            or new.terminal_at is distinct from old.terminal_at
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_command_mixed_mutation';
        end if;

        expected_command_type :=
            case
                when old.desired_state = 'RUNNING'
                    and new.desired_state = 'PAUSED'
                    then 'PAUSE'

                when old.desired_state = 'RUNNING'
                    and new.desired_state = 'STOPPED'
                    then 'STOP'

                when old.desired_state = 'PAUSED'
                    and new.desired_state = 'RUNNING'
                    then 'RESUME'

                when old.desired_state = 'PAUSED'
                    and new.desired_state = 'STOPPED'
                    then 'STOP'

                else null
            end;

        if expected_command_type is null then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_desired_transition_forbidden';
        end if;

        select command.command_type
        into administrative_command_type
        from public.operational_paper_session_run_commands as command
        where command.epoch_id = old.epoch_id
          and command.epoch_checksum = old.epoch_checksum
          and command.expected_record_version = old.record_version
          and command.resulting_record_version = new.record_version
          and command.desired_state = new.desired_state;

        if not found then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_command_required';
        end if;

        if administrative_command_type is distinct from expected_command_type then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_command_type_mismatch';
        end if;

        return new;
    end if;

    -- Worker/state mutations must never consume an administrative command
    -- record version.
    if exists (
        select 1
        from public.operational_paper_session_run_commands as command
        where command.epoch_id = old.epoch_id
          and command.resulting_record_version = new.record_version
    ) then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_unexpected_command_version';
    end if;

    -- No observed transition may bypass the frozen pure-domain graph.
    if new.observed_state is distinct from old.observed_state
        and not public.op_ps_run_transition_allowed(
            old.observed_state,
            new.observed_state
        )
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_observed_transition_forbidden';
    end if;

    -- Prevent a pure record-version bump with no semantic mutation.
    if new.observed_state is not distinct from old.observed_state
        and new.fencing_token is not distinct from old.fencing_token

        and new.worker_id is not distinct from old.worker_id
        and new.worker_claimed_at is not distinct from old.worker_claimed_at
        and new.worker_heartbeat_at is not distinct from old.worker_heartbeat_at
        and new.worker_lease_expires_at is not distinct from old.worker_lease_expires_at

        and new.failure_code is not distinct from old.failure_code
        and new.failure_at is not distinct from old.failure_at
        and new.terminal_at is not distinct from old.terminal_at
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_noop_mutation';
    end if;

    -- A new or reclaimed worker capability always advances the fencing token
    -- by exactly one.
    if new.fencing_token = old.fencing_token + 1 then
        if new.worker_id is null
            or new.worker_claimed_at is null
            or new.worker_heartbeat_at is null
            or new.worker_lease_expires_at is null
            or new.worker_claimed_at is distinct from new.worker_heartbeat_at
            or new.worker_claimed_at < new.start_requested_at
            or new.worker_lease_expires_at <= new.worker_heartbeat_at
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_new_claim_invalid';
        end if;

        if old.worker_id is null then
            -- Initial claim or resume claim.
            if old.observed_state not in ('PENDING', 'PAUSED')
                or old.desired_state <> 'RUNNING'
                or new.desired_state <> 'RUNNING'
                or new.observed_state <> 'STARTING'
            then
                raise exception using
                    errcode = '55000',
                    message = 'operational_paper_session_run_epoch_claim_transition_invalid';
            end if;
        else
            -- Crash recovery keeps the same epoch and advances the fence.
            if old.worker_lease_expires_at is null
                or new.worker_claimed_at < old.worker_lease_expires_at
                or new.observed_state <> 'RECOVERING'
                or new.desired_state is distinct from old.desired_state
            then
                raise exception using
                    errcode = '55000',
                    message = 'operational_paper_session_run_epoch_recovery_invalid';
            end if;
        end if;

        return new;
    end if;

    -- From here the fence is unchanged.
    if new.fencing_token is distinct from old.fencing_token then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_fence_mutation_invalid';
    end if;

    -- A worker cannot appear under an existing fence.
    if old.worker_id is null and new.worker_id is not null then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_worker_requires_new_fence';
    end if;

    if old.worker_id is not null and new.worker_id is not null then
        if new.worker_id is distinct from old.worker_id
            or new.worker_claimed_at is distinct from old.worker_claimed_at
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_worker_identity_changed';
        end if;

        -- Heartbeat and lease extension are one mutation.
        if (
            new.worker_heartbeat_at is distinct from old.worker_heartbeat_at
            or new.worker_lease_expires_at is distinct from old.worker_lease_expires_at
        ) then
            if new.worker_heartbeat_at is null
                or old.worker_heartbeat_at is null
                or new.worker_lease_expires_at is null
                or old.worker_lease_expires_at is null
                or new.worker_heartbeat_at <= old.worker_heartbeat_at
                or new.worker_heartbeat_at >= old.worker_lease_expires_at
                or new.worker_lease_expires_at <= old.worker_lease_expires_at
                or new.worker_heartbeat_at >= new.worker_lease_expires_at
            then
                raise exception using
                    errcode = '55000',
                    message = 'operational_paper_session_run_epoch_lease_renewal_invalid';
            end if;
        end if;
    end if;

    -- Releasing a capability is only a boundary settlement/failure.
    if old.worker_id is not null and new.worker_id is null then
        if new.observed_state not in ('PAUSED', 'STOPPED', 'FAILED') then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_worker_release_invalid';
        end if;
    end if;

    -- An unclaimed epoch can only settle/fail without introducing a worker.
    if old.worker_id is null and new.worker_id is null then
        if old.observed_state = 'PENDING'
            and new.observed_state not in (
                'PENDING',
                'PAUSED',
                'STOPPED',
                'FAILED'
            )
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_unclaimed_transition_invalid';
        end if;
    end if;

    -- Failure/terminal timestamps cannot precede the latest claimed activity.
    if old.worker_heartbeat_at is not null then
        if new.failure_at is not null
            and new.failure_at < old.worker_heartbeat_at
        then
            raise exception using
                errcode = '23514',
                message = 'operational_paper_session_run_epoch_failure_at_invalid';
        end if;

        if new.terminal_at is not null
            and new.terminal_at < old.worker_heartbeat_at
        then
            raise exception using
                errcode = '23514',
                message = 'operational_paper_session_run_epoch_terminal_at_invalid';
        end if;
    end if;

    return new;
end;
$function$;

comment on function public.protect_op_ps_run_epoch() is
    'Protects immutable run provenance, exact record-version advancement, desired/observed transitions and worker lease/fencing invariants.';

create trigger op_ps_run_epoch_protect
before update or delete
on public.operational_paper_session_run_epochs
for each row
execute function public.protect_op_ps_run_epoch();

revoke all privileges
    on function public.protect_op_ps_run_epoch()
    from public, anon, authenticated, service_role;

-- B1B2A-END


create function public.op_ps_run_command_allowed(
    observed_state text,
    command_type text
)
returns boolean
language sql
immutable
strict
set search_path = ''
as $function$
    select case command_type
        when 'START' then false
        when 'PAUSE' then
            observed_state in (
                'PENDING',
                'STARTING',
                'RUNNING',
                'RECOVERING'
            )
        when 'RESUME' then
            observed_state = 'PAUSED'
        when 'STOP' then
            observed_state in (
                'PENDING',
                'STARTING',
                'RUNNING',
                'PAUSED',
                'RECOVERING'
            )
        else false
    end;
$function$;

comment on function public.op_ps_run_command_allowed(text, text) is
    'Closed administrative command/state matrix for existing operational paper run epochs.';


create function public.validate_op_ps_run_command_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    epoch_record_version bigint;
    epoch_desired_state text;
    epoch_observed_state text;
    stored_epoch_checksum text;

    epoch_start_requested_by uuid;
    epoch_start_requested_at timestamptz;
    epoch_start_idempotency_key text;
    epoch_start_intent_fingerprint text;

    previous_requested_at timestamptz;
begin
    select
        epoch.record_version,
        epoch.desired_state,
        epoch.observed_state,
        epoch.epoch_checksum,
        epoch.start_requested_by,
        epoch.start_requested_at,
        epoch.start_idempotency_key,
        epoch.start_intent_fingerprint
    into
        epoch_record_version,
        epoch_desired_state,
        epoch_observed_state,
        stored_epoch_checksum,
        epoch_start_requested_by,
        epoch_start_requested_at,
        epoch_start_idempotency_key,
        epoch_start_intent_fingerprint
    from public.operational_paper_session_run_epochs as epoch
    where epoch.epoch_id = new.epoch_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_run_command_epoch_missing';
    end if;

    if stored_epoch_checksum is distinct from new.epoch_checksum then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_command_epoch_checksum_mismatch';
    end if;

    if new.command_type = 'START' then
        if epoch_record_version <> 1
            or epoch_desired_state <> 'RUNNING'
            or epoch_observed_state <> 'PENDING'

            or new.expected_record_version <> 0
            or new.resulting_record_version <> 1
            or new.desired_state <> 'RUNNING'

            or new.actor_id is distinct from epoch_start_requested_by
            or new.requested_at is distinct from epoch_start_requested_at
            or new.idempotency_key is distinct from epoch_start_idempotency_key
            or new.intent_fingerprint is distinct from epoch_start_intent_fingerprint
        then
            raise exception using
                errcode = '23514',
                message = 'operational_paper_session_run_start_command_mismatch';
        end if;

        return new;
    end if;

    if epoch_observed_state in ('STOPPED', 'FAILED') then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_command_epoch_terminal';
    end if;

    if not public.op_ps_run_command_allowed(
        epoch_observed_state,
        new.command_type
    ) then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_command_not_allowed';
    end if;

    if new.expected_record_version <> epoch_record_version
        or new.resulting_record_version <> epoch_record_version + 1
    then
        raise exception using
            errcode = '40001',
            message = 'operational_paper_session_run_command_record_version_conflict';
    end if;

    if (
        new.command_type = 'PAUSE'
        and (
            epoch_desired_state <> 'RUNNING'
            or new.desired_state <> 'PAUSED'
        )
    )
    or (
        new.command_type = 'RESUME'
        and (
            epoch_desired_state <> 'PAUSED'
            or new.desired_state <> 'RUNNING'
        )
    )
    or (
        new.command_type = 'STOP'
        and (
            epoch_desired_state not in ('RUNNING', 'PAUSED')
            or new.desired_state <> 'STOPPED'
        )
    )
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_command_desired_transition_invalid';
    end if;

    if new.requested_at < epoch_start_requested_at then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_command_requested_at_invalid';
    end if;

    select max(command.requested_at)
    into previous_requested_at
    from public.operational_paper_session_run_commands as command
    where command.epoch_id = new.epoch_id;

    if previous_requested_at is not null
        and new.requested_at < previous_requested_at
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_command_chronology_invalid';
    end if;

    return new;
end;
$function$;

comment on function public.validate_op_ps_run_command_insert() is
    'Validates append-only START/PAUSE/RESUME/STOP intent against the locked current epoch and exact record-version boundary.';

create trigger op_ps_run_command_validate_insert
before insert
on public.operational_paper_session_run_commands
for each row
execute function public.validate_op_ps_run_command_insert();


create function public.protect_op_ps_run_command()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception using
        errcode = '55000',
        message = case
            when tg_op = 'DELETE'
                then 'operational_paper_session_run_command_delete_forbidden'
            else 'operational_paper_session_run_command_update_forbidden'
        end;
end;
$function$;

comment on function public.protect_op_ps_run_command() is
    'Makes operational paper run command history strictly append-only.';

create trigger op_ps_run_command_protect
before update or delete
on public.operational_paper_session_run_commands
for each row
execute function public.protect_op_ps_run_command();


create function public.assert_op_ps_run_epoch_start_command()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if not exists (
        select 1
        from public.operational_paper_session_run_commands as command
        where command.epoch_id = new.epoch_id
          and command.epoch_checksum = new.epoch_checksum
          and command.command_type = 'START'
          and command.desired_state = 'RUNNING'
          and command.expected_record_version = 0
          and command.resulting_record_version = 1
          and command.actor_id = new.start_requested_by
          and command.requested_at = new.start_requested_at
          and command.idempotency_key = new.start_idempotency_key
          and command.intent_fingerprint = new.start_intent_fingerprint
    ) then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_start_command_required';
    end if;

    return null;
end;
$function$;

comment on function public.assert_op_ps_run_epoch_start_command() is
    'Deferred commit-time assertion that every newly persisted run epoch has its exact START command.';

create constraint trigger op_ps_run_epoch_start_command_required
after insert
on public.operational_paper_session_run_epochs
deferrable initially deferred
for each row
execute function public.assert_op_ps_run_epoch_start_command();


create function public.assert_op_ps_run_command_applied()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    epoch_record_version bigint;
    epoch_desired_state text;
    stored_epoch_checksum text;
begin
    select
        epoch.record_version,
        epoch.desired_state,
        epoch.epoch_checksum
    into
        epoch_record_version,
        epoch_desired_state,
        stored_epoch_checksum
    from public.operational_paper_session_run_epochs as epoch
    where epoch.epoch_id = new.epoch_id;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_run_command_epoch_missing_at_commit';
    end if;

    if stored_epoch_checksum is distinct from new.epoch_checksum then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_run_command_epoch_checksum_mismatch_at_commit';
    end if;

    if epoch_record_version <> new.resulting_record_version
        or epoch_desired_state <> new.desired_state
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_command_not_applied';
    end if;

    return null;
end;
$function$;

comment on function public.assert_op_ps_run_command_applied() is
    'Deferred commit-time assertion that each administrative command and its epoch desired-state mutation are one transaction.';

create constraint trigger op_ps_run_command_applied
after insert
on public.operational_paper_session_run_commands
deferrable initially deferred
for each row
execute function public.assert_op_ps_run_command_applied();


revoke all privileges
    on function
        public.op_ps_run_command_allowed(text, text),
        public.validate_op_ps_run_command_insert(),
        public.protect_op_ps_run_command(),
        public.assert_op_ps_run_epoch_start_command(),
        public.assert_op_ps_run_command_applied()
    from public, anon, authenticated, service_role;

-- B1B2B-END
