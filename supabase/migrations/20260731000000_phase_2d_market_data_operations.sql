-- ADT Phase 2D: backend-only operational catalog for RAW market-data work.
--
-- PostgreSQL owns administrative intent, queue state and bounded worker leases.
-- Candles, source payloads, filesystem paths and local execution journals do
-- not belong in this catalog.

create table public.market_data_operations (
    id uuid not null,
    operation_type text not null,
    exchange text not null,
    market text not null,
    symbol text not null,
    timeframe text not null,
    dataset_id text not null,
    range_start timestamptz not null,
    range_end timestamptz not null,
    plan_checksum text not null,
    request_fingerprint text not null,
    idempotency_key text not null,
    requested_by uuid not null,
    contract_version integer not null,
    status text not null,
    local_job_id text,
    chunks_planned integer not null,
    chunks_completed integer not null default 0,
    chunks_failed integer not null default 0,
    candles_estimated integer not null,
    candles_received integer not null default 0,
    candles_persisted integer not null default 0,
    requests_estimated integer not null,
    requests_completed integer not null default 0,
    progress_updated_at timestamptz not null,
    lease_owner uuid,
    lease_claimed_at timestamptz,
    lease_heartbeat_at timestamptz,
    lease_expires_at timestamptz,
    result_dataset_version text,
    result_dataset_checksum text,
    failure_code text,
    failure_message varchar(160),
    version bigint not null default 1,
    plan_created_at timestamptz not null,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    started_at timestamptz,
    finished_at timestamptz,
    constraint market_data_operations_pkey primary key (id),
    constraint market_data_operations_requested_by_fkey
        foreign key (requested_by)
        references auth.users (id),
    constraint market_data_operations_admin_idempotency_key_key
        unique (requested_by, idempotency_key),
    constraint market_data_operations_operation_type_check
        check (
            operation_type in (
                'RAW_BACKFILL',
                'RAW_INCREMENTAL_UPDATE'
            )
        ),
    constraint market_data_operations_identity_check
        check (
            exchange = 'binance'
            and market = 'spot'
            and symbol ~ '^[A-Z0-9][A-Z0-9._-]{0,31}/[A-Z0-9][A-Z0-9._-]{0,31}$'
            and split_part(symbol, '/', 1) <> split_part(symbol, '/', 2)
            and timeframe in ('1m', '5m', '1h', '1d')
            and dataset_id ~ '^[A-Za-z0-9_-]{1,192}$'
            and dataset_id = rtrim(
                translate(
                    replace(
                        encode(
                            convert_to(
                                exchange || ':' || market || ':' || symbol || ':' || timeframe,
                                'UTF8'
                            ),
                            'base64'
                        ),
                        chr(10),
                        ''
                    ),
                    '+/',
                    '-_'
                ),
                '='
            )
        ),
    constraint market_data_operations_range_check
        check (range_start < range_end),
    constraint market_data_operations_sha256_check
        check (
            plan_checksum ~ '^[0-9a-f]{64}$'
            and request_fingerprint ~ '^[0-9a-f]{64}$'
            and (
                result_dataset_version is null
                or result_dataset_version ~ '^[0-9a-f]{64}$'
            )
            and (
                result_dataset_checksum is null
                or result_dataset_checksum ~ '^[0-9a-f]{64}$'
            )
        ),
    constraint market_data_operations_idempotency_key_check
        check (
            idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and length(idempotency_key) <= 128
        ),
    constraint market_data_operations_contract_version_check
        check (contract_version = 1),
    constraint market_data_operations_status_check
        check (
            status in (
                'PENDING',
                'CLAIMED',
                'RUNNING',
                'PAUSE_REQUESTED',
                'PAUSED',
                'CANCEL_REQUESTED',
                'CANCELLED',
                'COMPLETED',
                'FAILED',
                'RECOVERING'
            )
        ),
    constraint market_data_operations_local_job_id_check
        check (
            local_job_id is null
            or (
                local_job_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
                and length(local_job_id) <= 64
            )
        ),
    constraint market_data_operations_progress_check
        check (
            chunks_planned >= 1
            and chunks_completed >= 0
            and chunks_failed >= 0
            and chunks_completed + chunks_failed <= chunks_planned
            and candles_estimated >= 1
            and candles_received >= 0
            and candles_persisted >= 0
            and candles_persisted <= candles_received
            and requests_estimated >= 0
            and requests_completed >= 0
        ),
    constraint market_data_operations_failure_code_check
        check (
            failure_code is null
            or failure_code in (
                'INVALID_REQUEST',
                'PLAN_CONFLICT',
                'DATASET_BUSY',
                'LEASE_LOST',
                'WORKER_UNAVAILABLE',
                'LOCAL_STATE_INVALID',
                'NETWORK_FAILURE',
                'RATE_LIMITED',
                'CANCELLED_BY_ADMIN',
                'INTERNAL_ERROR'
            )
        ),
    constraint market_data_operations_failure_message_check
        check (
            failure_message is null
            or failure_message = case failure_code
                when 'INVALID_REQUEST'
                    then 'A solicitação operacional é inválida.'
                when 'PLAN_CONFLICT'
                    then 'O plano local diverge da solicitação.'
                when 'DATASET_BUSY'
                    then 'O dataset está ocupado por outra operação.'
                when 'LEASE_LOST'
                    then 'A operação perdeu a lease do worker.'
                when 'WORKER_UNAVAILABLE'
                    then 'O worker não está disponível.'
                when 'LOCAL_STATE_INVALID'
                    then 'O estado local não pôde ser validado.'
                when 'NETWORK_FAILURE'
                    then 'A fonte pública não pôde ser acessada.'
                when 'RATE_LIMITED'
                    then 'A fonte pública limitou as requisições.'
                when 'CANCELLED_BY_ADMIN'
                    then 'A operação foi cancelada por administrador.'
                when 'INTERNAL_ERROR'
                    then 'A operação falhou de forma segura.'
            end
        ),
    constraint market_data_operations_version_check
        check (version >= 1),
    constraint market_data_operations_timestamps_check
        check (
            plan_created_at <= created_at
            and created_at <= updated_at
            and created_at <= progress_updated_at
            and progress_updated_at <= updated_at
            and (
                started_at is null
                or (
                    created_at <= started_at
                    and started_at <= updated_at
                )
            )
            and (
                finished_at is null
                or (
                    created_at <= finished_at
                    and finished_at <= updated_at
                )
            )
        ),
    constraint market_data_operations_lease_check
        check (
            (
                lease_owner is null
                and lease_claimed_at is null
                and lease_heartbeat_at is null
                and lease_expires_at is null
            )
            or (
                lease_owner is not null
                and lease_claimed_at is not null
                and lease_heartbeat_at is not null
                and lease_expires_at is not null
                and created_at <= lease_claimed_at
                and lease_claimed_at <= lease_heartbeat_at
                and lease_heartbeat_at <= updated_at
                and lease_heartbeat_at < lease_expires_at
                and status in (
                    'CLAIMED',
                    'RUNNING',
                    'PAUSE_REQUESTED',
                    'CANCEL_REQUESTED',
                    'RECOVERING'
                )
            )
        ),
    constraint market_data_operations_claimed_running_lease_check
        check (
            status not in ('CLAIMED', 'RUNNING')
            or (
                lease_owner is not null
                and started_at is not null
            )
        ),
    constraint market_data_operations_outcome_check
        check (
            (
                status = 'COMPLETED'
                and finished_at is not null
                and result_dataset_version is not null
                and result_dataset_checksum is not null
                and failure_code is null
                and failure_message is null
                and lease_owner is null
            )
            or (
                status = 'FAILED'
                and finished_at is not null
                and result_dataset_version is null
                and result_dataset_checksum is null
                and failure_code is not null
                and failure_code <> 'CANCELLED_BY_ADMIN'
                and failure_message is not null
                and lease_owner is null
            )
            or (
                status = 'CANCELLED'
                and finished_at is not null
                and result_dataset_version is null
                and result_dataset_checksum is null
                and failure_code = 'CANCELLED_BY_ADMIN'
                and failure_message is not null
                and lease_owner is null
            )
            or (
                status not in ('COMPLETED', 'FAILED', 'CANCELLED')
                and finished_at is null
                and result_dataset_version is null
                and result_dataset_checksum is null
                and failure_code is null
                and failure_message is null
            )
        )
);

comment on table public.market_data_operations is
    'Backend-only Phase 2D queue and sanitized operational state; never stores candles or source payloads.';
comment on constraint market_data_operations_requested_by_fkey
    on public.market_data_operations is
    'Attribution survives removal from app_admins; INSERT additionally requires current allow-list membership.';
comment on constraint market_data_operations_admin_idempotency_key_key
    on public.market_data_operations is
    'Idempotency keys are scoped to the requesting administrator to prevent cross-admin collisions and disclosure.';

-- The unique idempotency constraint and primary key already provide their
-- indexes. These indexes serve distinct queue and administrative read paths.
create index market_data_operations_claim_idx
    on public.market_data_operations (created_at, id)
    where status = 'PENDING';

create index market_data_operations_dataset_idx
    on public.market_data_operations (dataset_id, created_at desc, id desc);

create index market_data_operations_requested_by_idx
    on public.market_data_operations (requested_by, created_at desc, id desc);

create index market_data_operations_active_idx
    on public.market_data_operations (status, dataset_id, updated_at)
    where status in (
        'CLAIMED',
        'RUNNING',
        'PAUSE_REQUESTED',
        'CANCEL_REQUESTED',
        'RECOVERING'
    );

create index market_data_operations_expired_lease_idx
    on public.market_data_operations (lease_expires_at, id)
    where lease_owner is not null
      and status in (
          'CLAIMED',
          'RUNNING',
          'PAUSE_REQUESTED',
          'CANCEL_REQUESTED',
          'RECOVERING'
      );

create index market_data_operations_created_at_idx
    on public.market_data_operations (created_at desc, id desc);

-- Queue rows may accumulate for one dataset, but only one leased execution may
-- be active. This is the database-level backstop for concurrent claim races.
create unique index market_data_operations_one_active_dataset_uidx
    on public.market_data_operations (dataset_id)
    where status in ('CLAIMED', 'RUNNING', 'RECOVERING')
       or (
           status in ('PAUSE_REQUESTED', 'CANCEL_REQUESTED')
           and lease_owner is not null
       );

create unique index market_data_operations_one_active_owner_uidx
    on public.market_data_operations (lease_owner)
    where lease_owner is not null;

create function public.validate_market_data_operation_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if not exists (
        select 1
        from public.app_admins as admin
        where admin.user_id = new.requested_by
    ) then
        raise exception using
            errcode = '23503',
            message = 'market_data_operation_requester_not_admin';
    end if;

    return new;
end;
$function$;

comment on function public.validate_market_data_operation_insert() is
    'Requires current app_admins membership when backend code submits an operation.';

create trigger market_data_operations_validate_insert
before insert on public.market_data_operations
for each row
execute function public.validate_market_data_operation_insert();

create function public.protect_market_data_operation()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if old.status in ('CANCELLED', 'COMPLETED', 'FAILED') then
        raise exception using
            errcode = '55000',
            message = 'market_data_operation_terminal';
    end if;

    if new.id is distinct from old.id
        or new.operation_type is distinct from old.operation_type
        or new.exchange is distinct from old.exchange
        or new.market is distinct from old.market
        or new.symbol is distinct from old.symbol
        or new.timeframe is distinct from old.timeframe
        or new.dataset_id is distinct from old.dataset_id
        or new.range_start is distinct from old.range_start
        or new.range_end is distinct from old.range_end
        or new.plan_checksum is distinct from old.plan_checksum
        or new.request_fingerprint is distinct from old.request_fingerprint
        or new.idempotency_key is distinct from old.idempotency_key
        or new.requested_by is distinct from old.requested_by
        or new.contract_version is distinct from old.contract_version
        or new.chunks_planned is distinct from old.chunks_planned
        or new.candles_estimated is distinct from old.candles_estimated
        or new.requests_estimated is distinct from old.requests_estimated
        or new.plan_created_at is distinct from old.plan_created_at
        or new.created_at is distinct from old.created_at
        or (
            old.started_at is not null
            and new.started_at is distinct from old.started_at
        )
        or (
            old.started_at is null
            and new.started_at is not null
            and not (
                old.status = 'PENDING'
                and new.status = 'CLAIMED'
            )
        )
    then
        raise exception using
            errcode = '55000',
            message = 'market_data_operation_immutable';
    end if;

    if new.version is distinct from old.version + 1 then
        raise exception using
            errcode = '40001',
            message = 'market_data_operation_version_conflict';
    end if;

    if new.local_job_id is distinct from old.local_job_id
        and old.local_job_id is not null
    then
        raise exception using
            errcode = '55000',
            message = 'market_data_operation_immutable';
    end if;

    if new.chunks_completed < old.chunks_completed
        or new.chunks_failed < old.chunks_failed
        or new.candles_received < old.candles_received
        or new.candles_persisted < old.candles_persisted
        or new.requests_completed < old.requests_completed
        or new.progress_updated_at < old.progress_updated_at
        or new.updated_at < old.updated_at
    then
        raise exception using
            errcode = '23514',
            message = 'market_data_operation_progress_regression';
    end if;

    if new.lease_owner is not null and old.lease_owner is not null then
        if new.lease_owner is distinct from old.lease_owner
            or new.lease_claimed_at is distinct from old.lease_claimed_at
            or new.lease_heartbeat_at < old.lease_heartbeat_at
            or new.lease_expires_at < old.lease_expires_at
        then
            raise exception using
                errcode = '55000',
                message = 'market_data_operation_lease_invalid';
        end if;
    end if;

    if new.status is distinct from old.status
        and not (
            (old.status = 'PENDING' and new.status in (
                'CLAIMED', 'PAUSE_REQUESTED', 'CANCEL_REQUESTED'
            ))
            or (old.status = 'CLAIMED' and new.status in (
                'RUNNING', 'PAUSE_REQUESTED', 'CANCEL_REQUESTED',
                'FAILED', 'RECOVERING'
            ))
            or (old.status = 'RUNNING' and new.status in (
                'PAUSE_REQUESTED', 'CANCEL_REQUESTED', 'COMPLETED',
                'FAILED', 'RECOVERING', 'CANCELLED'
            ))
            or (old.status = 'PAUSE_REQUESTED' and new.status in (
                'PAUSED', 'COMPLETED', 'FAILED', 'RECOVERING'
            ))
            or (old.status = 'PAUSED' and new.status in (
                'PENDING', 'CANCEL_REQUESTED'
            ))
            or (old.status = 'CANCEL_REQUESTED' and new.status in (
                'CANCELLED', 'COMPLETED', 'FAILED', 'RECOVERING'
            ))
            or (old.status = 'RECOVERING' and new.status in (
                'CLAIMED', 'RUNNING', 'PAUSED', 'COMPLETED',
                'CANCELLED', 'FAILED'
            ))
        )
    then
        raise exception using
            errcode = '55000',
            message = 'market_data_operation_transition_invalid';
    end if;

    return new;
end;
$function$;

comment on function public.protect_market_data_operation() is
    'Enforces immutable intent, terminal state, monotonic progress, lease coherence and the approved transition graph.';

create trigger market_data_operations_protect_update
before update on public.market_data_operations
for each row
execute function public.protect_market_data_operation();

alter table public.market_data_operations enable row level security;

-- There are deliberately no RLS policies: the owner/direct backend connection
-- is the only access path. Explicit revokes also close PostgREST introspection
-- and access even if a future role is configured with BYPASSRLS.
revoke all privileges
    on table public.market_data_operations
    from public, anon, authenticated, service_role;

revoke all privileges
    on function
        public.validate_market_data_operation_insert(),
        public.protect_market_data_operation()
    from public, anon, authenticated, service_role;
