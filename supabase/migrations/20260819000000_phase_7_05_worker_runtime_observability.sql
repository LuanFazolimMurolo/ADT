-- ADT Phase 7-05: backend-only market-data worker runtime observability.
--
-- Worker runtime presence is intentionally independent from per-operation
-- leases. Runtime identifiers are internal correlation keys and must never be
-- exposed through the public or administrator HTTP contract.

create table public.market_data_worker_runtimes (
    id uuid not null,
    lifecycle_state text not null,
    activity_state text not null,
    started_at timestamptz not null,
    heartbeat_at timestamptz not null,
    stopped_at timestamptz,
    failure_code text,
    constraint market_data_worker_runtimes_pkey
        primary key (id),
    constraint market_data_worker_runtimes_lifecycle_state_check
        check (
            lifecycle_state in (
                'RUNNING',
                'STOPPED',
                'FAILED'
            )
        ),
    constraint market_data_worker_runtimes_activity_state_check
        check (
            activity_state in (
                'IDLE',
                'ACTIVE'
            )
        ),
    constraint market_data_worker_runtimes_failure_code_check
        check (
            (
                lifecycle_state = 'FAILED'
                and failure_code is not null
                and failure_code in (
                    'DATABASE_FAILURE',
                    'LOCAL_STATE_FAILURE',
                    'UNEXPECTED_FAILURE'
                )
            )
            or (
                lifecycle_state <> 'FAILED'
                and failure_code is null
            )
        ),
    constraint market_data_worker_runtimes_timestamps_check
        check (
            started_at <= heartbeat_at
            and (
                (
                    lifecycle_state = 'RUNNING'
                    and stopped_at is null
                )
                or (
                    lifecycle_state in ('STOPPED', 'FAILED')
                    and stopped_at is not null
                    and heartbeat_at <= stopped_at
                    and activity_state = 'IDLE'
                )
            )
        )
);

comment on table public.market_data_worker_runtimes is
    'Internal runtime epochs for backend market-data worker presence; stale heartbeat never proves process death.';

comment on column public.market_data_worker_runtimes.id is
    'Internal runtime epoch identifier; never expose this identifier through HTTP.';

create index market_data_worker_runtimes_heartbeat_idx
    on public.market_data_worker_runtimes (
        heartbeat_at desc,
        started_at desc,
        id desc
    );

create index market_data_worker_runtimes_running_heartbeat_idx
    on public.market_data_worker_runtimes (
        heartbeat_at desc,
        started_at desc,
        id desc
    )
    where lifecycle_state = 'RUNNING';


create table public.market_data_worker_events (
    id bigint generated always as identity,
    runtime_id uuid not null,
    operation_id uuid,
    event_type text not null,
    operation_state text,
    occurred_at timestamptz not null,
    constraint market_data_worker_events_pkey
        primary key (id),
    constraint market_data_worker_events_runtime_id_fkey
        foreign key (runtime_id)
        references public.market_data_worker_runtimes (id),
    constraint market_data_worker_events_operation_id_fkey
        foreign key (operation_id)
        references public.market_data_operations (id),
    constraint market_data_worker_events_event_type_check
        check (
            event_type in (
                'RUNTIME_STARTED',
                'RUNTIME_STOPPED',
                'RUNTIME_FAILED',
                'OPERATION_SETTLED'
            )
        ),
    constraint market_data_worker_events_operation_state_check
        check (
            operation_state is null
            or operation_state in (
                'PAUSED',
                'CANCELLED',
                'COMPLETED',
                'FAILED'
            )
        ),
    constraint market_data_worker_events_shape_check
        check (
            (
                event_type in (
                    'RUNTIME_STARTED',
                    'RUNTIME_STOPPED',
                    'RUNTIME_FAILED'
                )
                and operation_id is null
                and operation_state is null
            )
            or (
                event_type = 'OPERATION_SETTLED'
                and operation_id is not null
                and operation_state is not null
            )
        )
);

comment on table public.market_data_worker_events is
    'Append-only sanitized backend worker operational events.';

comment on column public.market_data_worker_events.runtime_id is
    'Internal runtime correlation identifier; never expose this identifier through HTTP.';

create index market_data_worker_events_occurred_at_idx
    on public.market_data_worker_events (
        occurred_at desc,
        id desc
    );

create index market_data_worker_events_runtime_idx
    on public.market_data_worker_events (
        runtime_id,
        occurred_at desc,
        id desc
    );

create index market_data_worker_events_operation_idx
    on public.market_data_worker_events (
        operation_id,
        occurred_at desc,
        id desc
    )
    where operation_id is not null;


create function public.validate_market_data_worker_runtime_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if new.lifecycle_state <> 'RUNNING'
        or new.activity_state <> 'IDLE'
        or new.started_at is distinct from new.heartbeat_at
        or new.stopped_at is not null
        or new.failure_code is not null
    then
        raise exception using
            errcode = '23514',
            message = 'market_data_worker_runtime_invalid_initial_state';
    end if;

    return new;
end;
$function$;

comment on function public.validate_market_data_worker_runtime_insert() is
    'Requires each worker runtime epoch to begin RUNNING and IDLE at one authoritative timestamp.';

create trigger market_data_worker_runtimes_validate_insert
before insert on public.market_data_worker_runtimes
for each row
execute function public.validate_market_data_worker_runtime_insert();


create function public.protect_market_data_worker_runtime()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'market_data_worker_runtime_delete_forbidden';
    end if;

    if new.id is distinct from old.id
        or new.started_at is distinct from old.started_at
    then
        raise exception using
            errcode = '55000',
            message = 'market_data_worker_runtime_identity_immutable';
    end if;

    if old.lifecycle_state in ('STOPPED', 'FAILED') then
        raise exception using
            errcode = '55000',
            message = 'market_data_worker_runtime_terminal';
    end if;

    if new.heartbeat_at < old.heartbeat_at then
        raise exception using
            errcode = '22007',
            message = 'market_data_worker_runtime_heartbeat_regression';
    end if;

    return new;
end;
$function$;

comment on function public.protect_market_data_worker_runtime() is
    'Protects runtime identity, history, terminal lifecycle state and monotonic heartbeat timestamps.';

create trigger market_data_worker_runtimes_protect_update_delete
before update or delete on public.market_data_worker_runtimes
for each row
execute function public.protect_market_data_worker_runtime();


create function public.reject_market_data_worker_event_change()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception using
        errcode = '55000',
        message = 'market_data_worker_events_append_only';
end;
$function$;

comment on function public.reject_market_data_worker_event_change() is
    'Rejects UPDATE and DELETE against append-only market-data worker events.';

create trigger market_data_worker_events_reject_update_delete
before update or delete on public.market_data_worker_events
for each row
execute function public.reject_market_data_worker_event_change();


alter table public.market_data_worker_runtimes enable row level security;
alter table public.market_data_worker_events enable row level security;

-- No RLS policies are intentionally created. These catalogs are backend-only
-- and are read through authenticated, sanitized application endpoints.
revoke all privileges
    on table
        public.market_data_worker_runtimes,
        public.market_data_worker_events
    from public, anon, authenticated, service_role;

revoke all privileges
    on sequence public.market_data_worker_events_id_seq
    from public, anon, authenticated, service_role;

revoke all privileges
    on function
        public.validate_market_data_worker_runtime_insert(),
        public.protect_market_data_worker_runtime(),
        public.reject_market_data_worker_event_change()
    from public, anon, authenticated, service_role;
