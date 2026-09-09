-- ADT Phase 7-12: backend-only operational market-data collector control.
--
-- One epoch is one durable administrative execution interval for one exact
-- immutable BINANCE_SPOT_RAW continuous-collector specification.
--
-- PostgreSQL owns administrative collector authority, desired/observed state,
-- worker lease and fencing. Existing local collector state and filesystem
-- exclusion remain separate local authorities.

create table public.operational_market_data_collector_epochs (
    epoch_id uuid primary key default gen_random_uuid(),

    schema_version integer not null,
    collector_contract_version integer not null,

    scope text not null,

    targets jsonb not null,
    interval_seconds integer not null,
    overlap_candles integer not null,
    specification_checksum text not null,

    desired_state text not null,
    observed_state text not null,

    record_version bigint not null,
    fencing_token bigint not null,

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

    constraint op_md_collector_epoch_start_actor_fkey
        foreign key (start_requested_by)
        references auth.users (id)
        on delete restrict,

    constraint op_md_collector_epoch_identity_key
        unique (epoch_id, epoch_checksum),

    constraint op_md_collector_epoch_actor_start_idempotency_key
        unique (start_requested_by, start_idempotency_key),

    constraint op_md_collector_epoch_schema_version_check
        check (schema_version = 1),

    constraint op_md_collector_epoch_contract_version_check
        check (collector_contract_version = 1),

    constraint op_md_collector_epoch_scope_check
        check (scope = 'BINANCE_SPOT_RAW'),

    constraint op_md_collector_epoch_targets_shape_check
        check (
            jsonb_typeof(targets) = 'array'
            and jsonb_array_length(targets) between 1 and 1000
        ),

    constraint op_md_collector_epoch_interval_check
        check (
            interval_seconds >= 1
            and interval_seconds <= 3600
        ),

    constraint op_md_collector_epoch_overlap_check
        check (
            overlap_candles >= 0
            and overlap_candles <= 100
        ),

    constraint op_md_collector_epoch_spec_checksum_check
        check (specification_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_md_collector_epoch_desired_state_check
        check (desired_state in ('RUNNING', 'PAUSED', 'STOPPED')),

    constraint op_md_collector_epoch_observed_state_check
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

    constraint op_md_collector_epoch_record_version_check
        check (record_version >= 1),

    constraint op_md_collector_epoch_fencing_token_check
        check (fencing_token >= 0),

    constraint op_md_collector_epoch_checksum_check
        check (epoch_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_md_collector_epoch_start_fingerprint_check
        check (start_intent_fingerprint ~ '^[0-9a-f]{64}$'),

    constraint op_md_collector_epoch_start_idempotency_key_check
        check (
            start_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(start_idempotency_key) <= 128
        ),

    constraint op_md_collector_epoch_failure_code_check
        check (
            failure_code is null
            or failure_code in (
                'COLLECTOR_SPEC_INVALID',
                'LOCAL_COLLECTOR_BUSY',
                'LOCAL_STATE_INVALID',
                'LEASE_LOST',
                'DATABASE_UNAVAILABLE',
                'INTERNAL_ERROR'
            )
        ),

    constraint op_md_collector_epoch_nonzero_epoch_id_check
        check (
            epoch_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_md_collector_epoch_nonzero_start_actor_check
        check (
            start_requested_by <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_md_collector_epoch_nonzero_worker_id_check
        check (
            worker_id is null
            or worker_id <>
                '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_md_collector_epoch_start_at_finite_check
        check (isfinite(start_requested_at)),

    constraint op_md_collector_epoch_worker_collective_check
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

    constraint op_md_collector_epoch_worker_finite_check
        check (
            (worker_claimed_at is null or isfinite(worker_claimed_at))
            and (
                worker_heartbeat_at is null
                or isfinite(worker_heartbeat_at)
            )
            and (
                worker_lease_expires_at is null
                or isfinite(worker_lease_expires_at)
            )
        ),

    constraint op_md_collector_epoch_worker_chronology_check
        check (
            worker_id is null
            or (
                worker_claimed_at <= worker_heartbeat_at
                and worker_heartbeat_at < worker_lease_expires_at
            )
        ),

    constraint op_md_collector_epoch_worker_fence_shape_check
        check (
            worker_id is null
            or fencing_token >= 1
        ),

    constraint op_md_collector_epoch_worker_state_shape_check
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

    constraint op_md_collector_epoch_failure_collective_check
        check (
            (failure_code is null and failure_at is null)
            or
            (failure_code is not null and failure_at is not null)
        ),

    constraint op_md_collector_epoch_failure_at_finite_check
        check (failure_at is null or isfinite(failure_at)),

    constraint op_md_collector_epoch_terminal_at_finite_check
        check (terminal_at is null or isfinite(terminal_at)),

    constraint op_md_collector_epoch_terminal_shape_check
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

    constraint op_md_collector_epoch_stopping_shape_check
        check (
            observed_state <> 'STOPPING'
            or desired_state = 'STOPPED'
        ),

    constraint op_md_collector_epoch_terminal_chronology_check
        check (
            terminal_at is null
            or terminal_at >= start_requested_at
        ),

    constraint op_md_collector_epoch_failure_chronology_check
        check (
            failure_at is null
            or failure_at >= start_requested_at
        )
);

comment on table public.operational_market_data_collector_epochs is
    'Durable historical control epochs for the immutable BINANCE_SPOT_RAW continuous collector specification, desired/observed state and worker fencing.';

create unique index op_md_collector_epoch_one_current_per_scope_uidx
    on public.operational_market_data_collector_epochs (scope)
    where observed_state not in ('STOPPED', 'FAILED');

create index op_md_collector_epoch_scope_history_idx
    on public.operational_market_data_collector_epochs (
        scope,
        start_requested_at desc,
        epoch_id desc
    );

create index op_md_collector_epoch_worker_lease_idx
    on public.operational_market_data_collector_epochs (
        worker_lease_expires_at,
        epoch_id
    )
    where worker_id is not null;

create index op_md_collector_epoch_list_idx
    on public.operational_market_data_collector_epochs (
        start_requested_at desc,
        epoch_id desc
    );


create table public.operational_market_data_collector_commands (
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

    constraint op_md_collector_command_epoch_identity_fkey
        foreign key (epoch_id, epoch_checksum)
        references public.operational_market_data_collector_epochs (
            epoch_id,
            epoch_checksum
        )
        on delete restrict,

    constraint op_md_collector_command_actor_fkey
        foreign key (actor_id)
        references auth.users (id)
        on delete restrict,

    constraint op_md_collector_command_actor_idempotency_key
        unique (actor_id, idempotency_key),

    constraint op_md_collector_command_epoch_result_version_key
        unique (epoch_id, resulting_record_version),

    constraint op_md_collector_command_contract_version_check
        check (command_contract_version = 1),

    constraint op_md_collector_command_type_check
        check (command_type in ('START', 'PAUSE', 'RESUME', 'STOP')),

    constraint op_md_collector_command_desired_state_check
        check (desired_state in ('RUNNING', 'PAUSED', 'STOPPED')),

    constraint op_md_collector_command_target_check
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

    constraint op_md_collector_command_version_check
        check (
            expected_record_version >= 0
            and resulting_record_version >= 1
            and resulting_record_version = expected_record_version + 1
        ),

    constraint op_md_collector_command_epoch_checksum_check
        check (epoch_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_md_collector_command_fingerprint_check
        check (intent_fingerprint ~ '^[0-9a-f]{64}$'),

    constraint op_md_collector_command_idempotency_key_check
        check (
            idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(idempotency_key) <= 128
        ),

    constraint op_md_collector_command_nonzero_command_id_check
        check (
            command_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_md_collector_command_nonzero_epoch_id_check
        check (
            epoch_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_md_collector_command_nonzero_actor_id_check
        check (
            actor_id <>
            '00000000-0000-0000-0000-000000000000'::uuid
        ),

    constraint op_md_collector_command_requested_at_finite_check
        check (isfinite(requested_at))
);

comment on table public.operational_market_data_collector_commands is
    'Append-only administrative START, PAUSE, RESUME and STOP intent records for operational market-data collector epochs.';

create index op_md_collector_command_epoch_history_idx
    on public.operational_market_data_collector_commands (
        epoch_id,
        resulting_record_version,
        command_id
    );

create index op_md_collector_command_list_idx
    on public.operational_market_data_collector_commands (
        requested_at desc,
        command_id desc
    );


alter table public.operational_market_data_collector_epochs
    enable row level security;

alter table public.operational_market_data_collector_commands
    enable row level security;

-- No RLS policies are created. Collector-control authority is backend-only
-- through the direct PostgreSQL owner connection.

revoke all privileges
    on table
        public.operational_market_data_collector_epochs,
        public.operational_market_data_collector_commands
    from public, anon, authenticated, service_role;

-- B1A-END


create function public.op_md_collector_transition_allowed(
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

comment on function public.op_md_collector_transition_allowed(text, text) is
    'Closed observed-state transition graph for operational market-data collector epochs.';

revoke all privileges
    on function public.op_md_collector_transition_allowed(text, text)
    from public, anon, authenticated, service_role;


create function public.validate_op_md_collector_epoch_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    target_value jsonb;
    target_symbol text;
    target_timeframe text;
    target_bootstrap text;
    target_key text;
    previous_target_key text := null;
begin
    -- Every genuine START begins one fresh historical epoch.
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
            message =
                'operational_market_data_collector_epoch_initial_state_invalid';
    end if;

    -- BEFORE INSERT triggers run before table CHECK constraints. Mirror the
    -- B1A top-level target-array contract here so malformed JSON fails closed
    -- as the same 23514 constraint contract instead of leaking a JSON runtime
    -- error from jsonb_array_elements().
    if pg_catalog.jsonb_typeof(new.targets) is distinct from 'array' then
        raise exception using
            errcode = '23514',
            message =
                'operational_market_data_collector_targets_shape_invalid',
            constraint =
                'op_md_collector_epoch_targets_shape_check';
    end if;

    if pg_catalog.jsonb_array_length(new.targets) < 1
        or pg_catalog.jsonb_array_length(new.targets) > 1000
    then
        raise exception using
            errcode = '23514',
            message =
                'operational_market_data_collector_targets_shape_invalid',
            constraint =
                'op_md_collector_epoch_targets_shape_check';
    end if;

    -- Every individual target is then validated before persistence.
    for target_value in
        select element.value
        from pg_catalog.jsonb_array_elements(new.targets)
            with ordinality as element(value, ordinal)
        order by element.ordinal
    loop
        if pg_catalog.jsonb_typeof(target_value) <> 'object'
            or not (target_value ? 'symbol')
            or not (target_value ? 'timeframe')
            or not (target_value ? 'bootstrap_candles')
            or exists (
                select 1
                from pg_catalog.jsonb_object_keys(target_value)
                    as target_field(name)
                where target_field.name not in (
                    'symbol',
                    'timeframe',
                    'bootstrap_candles'
                )
            )
        then
            raise exception using
                errcode = '23514',
                message =
                    'operational_market_data_collector_target_shape_invalid';
        end if;

        if pg_catalog.jsonb_typeof(target_value -> 'symbol') <> 'string'
            or pg_catalog.jsonb_typeof(target_value -> 'timeframe') <> 'string'
            or pg_catalog.jsonb_typeof(
                target_value -> 'bootstrap_candles'
            ) <> 'number'
        then
            raise exception using
                errcode = '23514',
                message =
                    'operational_market_data_collector_target_type_invalid';
        end if;

        target_symbol := target_value ->> 'symbol';
        target_timeframe := target_value ->> 'timeframe';
        target_bootstrap := target_value ->> 'bootstrap_candles';

        -- Mirror TradingPair canonical BASE/QUOTE representation.
        if target_symbol !~
            '^[A-Z0-9][A-Z0-9._-]{0,31}/[A-Z0-9][A-Z0-9._-]{0,31}$'
            or pg_catalog.split_part(target_symbol, '/', 1)
                = pg_catalog.split_part(target_symbol, '/', 2)
        then
            raise exception using
                errcode = '23514',
                message =
                    'operational_market_data_collector_target_symbol_invalid';
        end if;

        if target_timeframe not in (
            '1m',
            '5m',
            '15m',
            '30m',
            '1h',
            '4h',
            '12h',
            '1d',
            '1w'
        ) then
            raise exception using
                errcode = '23514',
                message =
                    'operational_market_data_collector_target_timeframe_invalid';
        end if;

        -- Require an exact JSON integer representation, never decimal,
        -- exponent, boolean or string.
        if target_bootstrap !~ '^[0-9]+$'
            or target_bootstrap::numeric < 1
            or target_bootstrap::numeric > 1000000
        then
            raise exception using
                errcode = '23514',
                message =
                    'operational_market_data_collector_target_bootstrap_invalid';
        end if;

        target_key := target_symbol || ':' || target_timeframe;

        if previous_target_key is not null
            and target_key <= previous_target_key
        then
            raise exception using
                errcode = '23514',
                message =
                    'operational_market_data_collector_targets_order_invalid';
        end if;

        previous_target_key := target_key;
    end loop;

    return new;
end;
$function$;

comment on function public.validate_op_md_collector_epoch_insert() is
    'Validates fresh START shape and the exact ordered immutable collector target specification before epoch persistence.';

create trigger op_md_collector_epoch_validate_insert
before insert
on public.operational_market_data_collector_epochs
for each row
execute function public.validate_op_md_collector_epoch_insert();

revoke all privileges
    on function public.validate_op_md_collector_epoch_insert()
    from public, anon, authenticated, service_role;

-- B1B1-END


create function public.protect_op_md_collector_epoch()
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
            message =
                'operational_market_data_collector_epoch_delete_forbidden';
    end if;

    -- Historical identity and the exact frozen collector specification
    -- are immutable for the lifetime of one epoch.
    if new.epoch_id is distinct from old.epoch_id
        or new.schema_version is distinct from old.schema_version
        or new.collector_contract_version
            is distinct from old.collector_contract_version
        or new.scope is distinct from old.scope

        or new.targets is distinct from old.targets
        or new.interval_seconds is distinct from old.interval_seconds
        or new.overlap_candles is distinct from old.overlap_candles
        or new.specification_checksum
            is distinct from old.specification_checksum

        or new.epoch_checksum is distinct from old.epoch_checksum

        or new.start_requested_by is distinct from old.start_requested_by
        or new.start_requested_at is distinct from old.start_requested_at
        or new.start_idempotency_key
            is distinct from old.start_idempotency_key
        or new.start_intent_fingerprint
            is distinct from old.start_intent_fingerprint
    then
        raise exception using
            errcode = '55000',
            message =
                'operational_market_data_collector_epoch_immutable_fields_changed';
    end if;

    if new.record_version is distinct from old.record_version + 1 then
        raise exception using
            errcode = '40001',
            message =
                'operational_market_data_collector_epoch_record_version_conflict';
    end if;

    if old.observed_state in ('STOPPED', 'FAILED') then
        raise exception using
            errcode = '55000',
            message =
                'operational_market_data_collector_epoch_terminal';
    end if;

    if new.fencing_token < old.fencing_token
        or new.fencing_token > old.fencing_token + 1
    then
        raise exception using
            errcode = '55000',
            message =
                'operational_market_data_collector_epoch_fencing_token_invalid';
    end if;

    -- Desired-state mutations are reserved for an exact administrative
    -- command record. B1B2B will independently validate and protect the
    -- command history itself.
    if new.desired_state is distinct from old.desired_state then
        if new.observed_state is distinct from old.observed_state
            or new.fencing_token is distinct from old.fencing_token

            or new.worker_id is distinct from old.worker_id
            or new.worker_claimed_at
                is distinct from old.worker_claimed_at
            or new.worker_heartbeat_at
                is distinct from old.worker_heartbeat_at
            or new.worker_lease_expires_at
                is distinct from old.worker_lease_expires_at

            or new.failure_code is distinct from old.failure_code
            or new.failure_at is distinct from old.failure_at
            or new.terminal_at is distinct from old.terminal_at
        then
            raise exception using
                errcode = '55000',
                message =
                    'operational_market_data_collector_epoch_command_mixed_mutation';
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
                message =
                    'operational_market_data_collector_epoch_desired_transition_forbidden';
        end if;

        select command.command_type
        into administrative_command_type
        from public.operational_market_data_collector_commands
            as command
        where command.epoch_id = old.epoch_id
          and command.epoch_checksum = old.epoch_checksum
          and command.expected_record_version = old.record_version
          and command.resulting_record_version = new.record_version
          and command.desired_state = new.desired_state;

        if not found then
            raise exception using
                errcode = '55000',
                message =
                    'operational_market_data_collector_epoch_command_required';
        end if;

        if administrative_command_type
            is distinct from expected_command_type
        then
            raise exception using
                errcode = '55000',
                message =
                    'operational_market_data_collector_epoch_command_type_mismatch';
        end if;

        return new;
    end if;

    -- A worker/state mutation must never consume a record-version already
    -- reserved by an administrative command.
    if exists (
        select 1
        from public.operational_market_data_collector_commands
            as command
        where command.epoch_id = old.epoch_id
          and command.resulting_record_version = new.record_version
    ) then
        raise exception using
            errcode = '55000',
            message =
                'operational_market_data_collector_epoch_unexpected_command_version';
    end if;

    if new.observed_state is distinct from old.observed_state
        and not public.op_md_collector_transition_allowed(
            old.observed_state,
            new.observed_state
        )
    then
        raise exception using
            errcode = '55000',
            message =
                'operational_market_data_collector_epoch_observed_transition_forbidden';
    end if;

    -- A pure record-version bump has no semantic meaning.
    if new.observed_state is not distinct from old.observed_state
        and new.fencing_token is not distinct from old.fencing_token

        and new.worker_id is not distinct from old.worker_id
        and new.worker_claimed_at
            is not distinct from old.worker_claimed_at
        and new.worker_heartbeat_at
            is not distinct from old.worker_heartbeat_at
        and new.worker_lease_expires_at
            is not distinct from old.worker_lease_expires_at

        and new.failure_code is not distinct from old.failure_code
        and new.failure_at is not distinct from old.failure_at
        and new.terminal_at is not distinct from old.terminal_at
    then
        raise exception using
            errcode = '55000',
            message =
                'operational_market_data_collector_epoch_noop_mutation';
    end if;

    -- Every initial claim, resume claim or crash-recovery reclaim advances
    -- the capability fence by exactly one.
    if new.fencing_token = old.fencing_token + 1 then
        if new.worker_id is null
            or new.worker_claimed_at is null
            or new.worker_heartbeat_at is null
            or new.worker_lease_expires_at is null
            or new.worker_claimed_at
                is distinct from new.worker_heartbeat_at
            or new.worker_claimed_at < new.start_requested_at
            or new.worker_lease_expires_at
                <= new.worker_heartbeat_at
        then
            raise exception using
                errcode = '55000',
                message =
                    'operational_market_data_collector_epoch_new_claim_invalid';
        end if;

        if old.worker_id is null then
            -- Initial claim or claim after a settled PAUSE.
            if old.observed_state not in ('PENDING', 'PAUSED')
                or old.desired_state <> 'RUNNING'
                or new.desired_state <> 'RUNNING'
                or new.observed_state <> 'STARTING'
            then
                raise exception using
                    errcode = '55000',
                    message =
                        'operational_market_data_collector_epoch_claim_transition_invalid';
            end if;
        else
            -- Crash recovery stays in the same epoch. The old lease must
            -- already be expired at the new claim instant.
            if old.worker_lease_expires_at is null
                or new.worker_claimed_at
                    < old.worker_lease_expires_at
                or new.observed_state <> 'RECOVERING'
                or new.desired_state
                    is distinct from old.desired_state
            then
                raise exception using
                    errcode = '55000',
                    message =
                        'operational_market_data_collector_epoch_recovery_invalid';
            end if;
        end if;

        return new;
    end if;

    -- From here onward the current fence must remain unchanged.
    if new.fencing_token is distinct from old.fencing_token then
        raise exception using
            errcode = '55000',
            message =
                'operational_market_data_collector_epoch_fence_mutation_invalid';
    end if;

    if old.worker_id is null
        and new.worker_id is not null
    then
        raise exception using
            errcode = '55000',
            message =
                'operational_market_data_collector_epoch_worker_requires_new_fence';
    end if;

    if old.worker_id is not null
        and new.worker_id is not null
    then
        if new.worker_id is distinct from old.worker_id
            or new.worker_claimed_at
                is distinct from old.worker_claimed_at
        then
            raise exception using
                errcode = '55000',
                message =
                    'operational_market_data_collector_epoch_worker_identity_changed';
        end if;

        -- Heartbeat advancement and lease extension are one atomic mutation.
        if new.worker_heartbeat_at
                is distinct from old.worker_heartbeat_at
            or new.worker_lease_expires_at
                is distinct from old.worker_lease_expires_at
        then
            if new.worker_heartbeat_at is null
                or old.worker_heartbeat_at is null
                or new.worker_lease_expires_at is null
                or old.worker_lease_expires_at is null

                or new.worker_heartbeat_at
                    <= old.worker_heartbeat_at
                or new.worker_heartbeat_at
                    >= old.worker_lease_expires_at
                or new.worker_lease_expires_at
                    <= old.worker_lease_expires_at
                or new.worker_heartbeat_at
                    >= new.worker_lease_expires_at
            then
                raise exception using
                    errcode = '55000',
                    message =
                        'operational_market_data_collector_epoch_lease_renewal_invalid';
            end if;
        end if;
    end if;

    -- Releasing a PostgreSQL capability is only legal while settling a
    -- complete cycle boundary or failing the epoch.
    if old.worker_id is not null
        and new.worker_id is null
    then
        if new.observed_state not in (
            'PAUSED',
            'STOPPED',
            'FAILED'
        ) then
            raise exception using
                errcode = '55000',
                message =
                    'operational_market_data_collector_epoch_worker_release_invalid';
        end if;
    end if;

    -- An unclaimed fresh epoch may settle PAUSED/STOPPED or fail, but can
    -- never jump into a worker-owned running state without a new fence.
    if old.worker_id is null
        and new.worker_id is null
    then
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
                message =
                    'operational_market_data_collector_epoch_unclaimed_transition_invalid';
        end if;
    end if;

    -- Failure/terminal evidence cannot be backdated before the most recent
    -- heartbeat of the capability being released.
    if old.worker_heartbeat_at is not null then
        if new.failure_at is not null
            and new.failure_at < old.worker_heartbeat_at
        then
            raise exception using
                errcode = '23514',
                message =
                    'operational_market_data_collector_epoch_failure_at_invalid';
        end if;

        if new.terminal_at is not null
            and new.terminal_at < old.worker_heartbeat_at
        then
            raise exception using
                errcode = '23514',
                message =
                    'operational_market_data_collector_epoch_terminal_at_invalid';
        end if;
    end if;

    return new;
end;
$function$;

comment on function public.protect_op_md_collector_epoch() is
    'Protects immutable collector specification, exact record-version advancement, observed-state transitions and worker lease/fencing invariants.';

create trigger op_md_collector_epoch_protect
before update or delete
on public.operational_market_data_collector_epochs
for each row
execute function public.protect_op_md_collector_epoch();

revoke all privileges
    on function public.protect_op_md_collector_epoch()
    from public, anon, authenticated, service_role;

-- B1B2A-END


create function public.op_md_collector_command_allowed(
    desired_state text,
    observed_state text,
    command_type text
)
returns boolean
language sql
immutable
strict
set search_path = ''
as $function$
    select
        observed_state not in ('STOPPED', 'FAILED')
        and case command_type
            when 'START' then false
            when 'PAUSE' then
                desired_state = 'RUNNING'
            when 'RESUME' then
                desired_state = 'PAUSED'
            when 'STOP' then
                desired_state in ('RUNNING', 'PAUSED')
            else false
        end;
$function$;

comment on function
    public.op_md_collector_command_allowed(text, text, text) is
    'Closed desired-state administrative command matrix for non-terminal operational market-data collector epochs.';

revoke all privileges
    on function
        public.op_md_collector_command_allowed(text, text, text)
    from public, anon, authenticated, service_role;


create function public.validate_op_md_collector_command_insert()
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
    from public.operational_market_data_collector_epochs as epoch
    where epoch.epoch_id = new.epoch_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message =
                'operational_market_data_collector_command_epoch_missing';
    end if;

    if stored_epoch_checksum is distinct from new.epoch_checksum then
        raise exception using
            errcode = '23514',
            message =
                'operational_market_data_collector_command_epoch_checksum_mismatch';
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
            or new.idempotency_key
                is distinct from epoch_start_idempotency_key
            or new.intent_fingerprint
                is distinct from epoch_start_intent_fingerprint
        then
            raise exception using
                errcode = '23514',
                message =
                    'operational_market_data_collector_start_command_mismatch';
        end if;

        return new;
    end if;

    if not public.op_md_collector_command_allowed(
        epoch_desired_state,
        epoch_observed_state,
        new.command_type
    ) then
        raise exception using
            errcode = '55000',
            message =
                'operational_market_data_collector_command_not_allowed';
    end if;

    if new.expected_record_version <> epoch_record_version
        or new.resulting_record_version <> epoch_record_version + 1
    then
        raise exception using
            errcode = '40001',
            message =
                'operational_market_data_collector_command_record_version_conflict';
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
            message =
                'operational_market_data_collector_command_desired_transition_invalid';
    end if;

    if new.requested_at < epoch_start_requested_at then
        raise exception using
            errcode = '23514',
            message =
                'operational_market_data_collector_command_requested_at_invalid';
    end if;

    select max(command.requested_at)
    into previous_requested_at
    from public.operational_market_data_collector_commands
        as command
    where command.epoch_id = new.epoch_id;

    if previous_requested_at is not null
        and new.requested_at < previous_requested_at
    then
        raise exception using
            errcode = '23514',
            message =
                'operational_market_data_collector_command_chronology_invalid';
    end if;

    return new;
end;
$function$;

comment on function public.validate_op_md_collector_command_insert() is
    'Validates append-only collector command intent against the locked desired/observed epoch state and exact record-version boundary.';

create trigger op_md_collector_command_validate_insert
before insert
on public.operational_market_data_collector_commands
for each row
execute function public.validate_op_md_collector_command_insert();

revoke all privileges
    on function public.validate_op_md_collector_command_insert()
    from public, anon, authenticated, service_role;


create function public.protect_op_md_collector_command()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception using
        errcode = '55000',
        message = case
            when tg_op = 'DELETE'
                then
                    'operational_market_data_collector_command_delete_forbidden'
            else
                'operational_market_data_collector_command_update_forbidden'
        end;
end;
$function$;

comment on function public.protect_op_md_collector_command() is
    'Makes operational market-data collector command history strictly append-only.';

create trigger op_md_collector_command_protect
before update or delete
on public.operational_market_data_collector_commands
for each row
execute function public.protect_op_md_collector_command();

revoke all privileges
    on function public.protect_op_md_collector_command()
    from public, anon, authenticated, service_role;


create function public.assert_op_md_collector_epoch_start_command()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if not exists (
        select 1
        from public.operational_market_data_collector_commands
            as command
        where command.epoch_id = new.epoch_id
          and command.epoch_checksum = new.epoch_checksum
          and command.command_contract_version = 1
          and command.command_type = 'START'
          and command.desired_state = 'RUNNING'
          and command.expected_record_version = 0
          and command.resulting_record_version = 1
          and command.actor_id = new.start_requested_by
          and command.requested_at = new.start_requested_at
          and command.idempotency_key = new.start_idempotency_key
          and command.intent_fingerprint =
              new.start_intent_fingerprint
    ) then
        raise exception using
            errcode = '23514',
            message =
                'operational_market_data_collector_epoch_start_command_required';
    end if;

    return new;
end;
$function$;

comment on function
    public.assert_op_md_collector_epoch_start_command() is
    'Requires each collector epoch to have its exact START command in the same transaction.';

create constraint trigger op_md_collector_epoch_start_command_required
after insert
on public.operational_market_data_collector_epochs
deferrable initially deferred
for each row
execute function public.assert_op_md_collector_epoch_start_command();

revoke all privileges
    on function public.assert_op_md_collector_epoch_start_command()
    from public, anon, authenticated, service_role;


create function public.assert_op_md_collector_command_applied()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    current_record_version bigint;
    current_desired_state text;
    current_epoch_checksum text;
begin
    select
        epoch.record_version,
        epoch.desired_state,
        epoch.epoch_checksum
    into
        current_record_version,
        current_desired_state,
        current_epoch_checksum
    from public.operational_market_data_collector_epochs as epoch
    where epoch.epoch_id = new.epoch_id;

    if not found then
        raise exception using
            errcode = '23503',
            message =
                'operational_market_data_collector_command_epoch_missing';
    end if;

    if current_epoch_checksum is distinct from new.epoch_checksum
        or current_record_version
            is distinct from new.resulting_record_version
        or current_desired_state
            is distinct from new.desired_state
    then
        raise exception using
            errcode = '23514',
            message =
                'operational_market_data_collector_command_not_applied';
    end if;

    return new;
end;
$function$;

comment on function public.assert_op_md_collector_command_applied() is
    'Requires each collector command to match the committed desired state and record-version boundary of its epoch.';

create constraint trigger op_md_collector_command_applied
after insert
on public.operational_market_data_collector_commands
deferrable initially deferred
for each row
execute function public.assert_op_md_collector_command_applied();

revoke all privileges
    on function public.assert_op_md_collector_command_applied()
    from public, anon, authenticated, service_role;

-- B1B2B-END
