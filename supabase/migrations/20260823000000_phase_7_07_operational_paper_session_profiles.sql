-- ADT Phase 7-07: backend-only operational paper-session profile authority.
--
-- Profile specifications are immutable resolved revisions. The aggregate
-- publishes exactly one current revision and may seal it through a one-way
-- approval/archive lifecycle. Capital, materialization and runtime identity
-- remain outside this schema.

create table public.operational_paper_session_profiles (
    profile_id uuid primary key default gen_random_uuid(),
    state text not null,
    current_revision bigint not null,
    record_version bigint not null,
    approved_revision bigint,
    approved_checksum text,
    created_by uuid not null,
    created_at timestamptz not null,
    approved_by uuid,
    approved_at timestamptz,
    archived_by uuid,
    archived_at timestamptz,
    create_idempotency_key text not null,
    create_intent_fingerprint text not null,
    constraint operational_paper_session_profiles_created_by_fkey
        foreign key (created_by) references auth.users (id) on delete restrict,
    constraint operational_paper_session_profiles_approved_by_fkey
        foreign key (approved_by) references auth.users (id) on delete restrict,
    constraint operational_paper_session_profiles_archived_by_fkey
        foreign key (archived_by) references auth.users (id) on delete restrict,
    constraint operational_paper_session_profiles_actor_idempotency_key
        unique (created_by, create_idempotency_key),
    constraint operational_paper_session_profiles_state_check
        check (state in ('DRAFT', 'APPROVED', 'ARCHIVED')),
    constraint operational_paper_session_profiles_revision_check
        check (current_revision >= 1),
    constraint operational_paper_session_profiles_record_version_check
        check (record_version >= 1),
    constraint operational_paper_session_profiles_idempotency_key_check
        check (
            create_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(create_idempotency_key) <= 128
        ),
    constraint operational_paper_session_profiles_fingerprint_check
        check (create_intent_fingerprint ~ '^[0-9a-f]{64}$'),
    constraint operational_paper_session_profiles_approved_checksum_check
        check (approved_checksum is null or approved_checksum ~ '^[0-9a-f]{64}$'),
    constraint operational_paper_session_profiles_approval_collective_check
        check (
            (
                approved_revision is null
                and approved_checksum is null
                and approved_by is null
                and approved_at is null
            )
            or (
                approved_revision is not null
                and approved_checksum is not null
                and approved_by is not null
                and approved_at is not null
            )
        ),
    constraint operational_paper_session_profiles_archive_collective_check
        check (
            (archived_by is null and archived_at is null)
            or (archived_by is not null and archived_at is not null)
        ),
    constraint operational_paper_session_profiles_state_shape_check
        check (
            (state = 'DRAFT' and approved_revision is null and archived_by is null)
            or (state = 'APPROVED' and approved_revision is not null and archived_by is null)
            or (state = 'ARCHIVED' and archived_by is not null)
        ),
    constraint operational_paper_session_profiles_approved_revision_check
        check (approved_revision is null or approved_revision = current_revision),
    constraint operational_paper_session_profiles_chronology_check
        check (
            (approved_at is null or approved_at >= created_at)
            and (archived_at is null or archived_at >= created_at)
            and (
                approved_at is null
                or archived_at is null
                or archived_at >= approved_at
            )
        )
);


create table public.operational_paper_session_profile_revisions (
    profile_id uuid not null,
    revision bigint not null,
    schema_version integer not null,
    specification_checksum text not null,
    name text not null,
    description text not null,
    mandate_id uuid not null,
    mandate_approved_revision bigint not null,
    mandate_specification_checksum text not null,
    exchange text not null,
    market_type text not null,
    base_asset text not null,
    quote_asset text not null,
    timeframe text not null,
    start_at timestamptz not null,
    warmup_candles bigint not null,
    strategy_definition_id uuid not null,
    strategy_source_revision bigint not null,
    strategy_plugin_name text not null,
    strategy_plugin_version text not null,
    strategy_plugin_schema_version integer not null,
    strategy_lifecycle_version integer not null,
    strategy_parameters jsonb not null,
    strategy_parameters_checksum text not null,
    strategy_snapshot_checksum text not null,
    strategy_snapshot_schema_version integer not null,
    execution jsonb not null,
    instrument_constraints jsonb not null,
    risk_limits jsonb not null,
    history_window bigint not null,
    max_candles bigint not null,
    max_orders bigint not null,
    max_events bigint not null,
    engine_version text not null,
    market_regime_policy jsonb,
    created_by uuid not null,
    created_at timestamptz not null,
    constraint operational_paper_session_profile_revisions_pkey
        primary key (profile_id, revision),
    constraint operational_paper_session_profile_revisions_checksum_key
        unique (profile_id, revision, specification_checksum),
    constraint operational_paper_session_profile_revisions_profile_id_fkey
        foreign key (profile_id)
        references public.operational_paper_session_profiles (profile_id)
        on delete restrict
        deferrable initially deferred,
    constraint operational_paper_session_profile_revisions_created_by_fkey
        foreign key (created_by) references auth.users (id) on delete restrict,
    constraint operational_paper_session_profile_revisions_mandate_fkey
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
    constraint operational_paper_session_profile_revisions_instrument_fkey
        foreign key (
            mandate_id,
            mandate_approved_revision,
            exchange,
            market_type,
            base_asset,
            quote_asset
        )
        references public.operational_mandate_revision_instruments (
            mandate_id,
            revision,
            exchange,
            market_type,
            base_asset,
            quote_asset
        )
        on delete restrict,
    constraint operational_paper_session_profile_revisions_strategy_fkey
        foreign key (strategy_definition_id)
        references public.strategy_definitions (id)
        on delete restrict,
    constraint operational_paper_session_profile_revisions_revision_check
        check (revision >= 1),
    constraint op_ps_profile_revisions_schema_version_check
        check (schema_version = 1),
    constraint operational_paper_session_profile_revisions_checksum_check
        check (specification_checksum ~ '^[0-9a-f]{64}$'),
    constraint operational_paper_session_profile_revisions_name_check
        check (
            char_length(name) between 1 and 120
            and name = btrim(name, ' ')
            and position(chr(13) in name) = 0
        ),
    constraint operational_paper_session_profile_revisions_description_check
        check (
            char_length(description) <= 1000
            and description = btrim(description, ' ')
            and position(chr(13) in description) = 0
        ),
    constraint op_ps_profile_revisions_mandate_checksum_check
        check (mandate_specification_checksum ~ '^[0-9a-f]{64}$'),
    constraint operational_paper_session_profile_revisions_capability_check
        check (exchange = 'binance' and market_type = 'spot'),
    constraint operational_paper_session_profile_revisions_assets_check
        check (
            base_asset ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'
            and quote_asset ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'
            and base_asset <> quote_asset
        ),
    constraint operational_paper_session_profile_revisions_timeframe_check
        check (
            timeframe in ('1m', '5m', '15m', '30m', '1h', '4h', '12h', '1d', '1w')
        ),
    constraint operational_paper_session_profile_revisions_warmup_check
        check (warmup_candles between 0 and 100000),
    constraint op_ps_profile_revisions_strategy_source_revision_check
        check (strategy_source_revision >= 1),
    constraint op_ps_profile_revisions_strategy_plugin_name_check
        check (strategy_plugin_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    constraint op_ps_profile_revisions_strategy_plugin_version_check
        check (strategy_plugin_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    constraint op_ps_profile_revisions_strategy_plugin_schema_check
        check (strategy_plugin_schema_version >= 1),
    constraint op_ps_profile_revisions_strategy_lifecycle_check
        check (strategy_lifecycle_version in (1, 2)),
    constraint op_ps_profile_revisions_strategy_parameters_check
        check (jsonb_typeof(strategy_parameters) = 'array'),
    constraint op_ps_profile_revisions_strategy_parameters_checksum_check
        check (strategy_parameters_checksum ~ '^[0-9a-f]{64}$'),
    constraint op_ps_profile_revisions_strategy_snapshot_checksum_check
        check (strategy_snapshot_checksum ~ '^[0-9a-f]{64}$'),
    constraint op_ps_profile_revisions_strategy_snapshot_schema_check
        check (strategy_snapshot_schema_version = 1),
    constraint operational_paper_session_profile_revisions_execution_check
        check (jsonb_typeof(execution) = 'object'),
    constraint op_ps_profile_revisions_instrument_constraints_check
        check (jsonb_typeof(instrument_constraints) = 'object'),
    constraint operational_paper_session_profile_revisions_risk_limits_check
        check (jsonb_typeof(risk_limits) = 'object'),
    constraint op_ps_profile_revisions_history_window_check
        check (history_window between 1 and 100000),
    constraint operational_paper_session_profile_revisions_max_candles_check
        check (max_candles between 1 and 2000000),
    constraint operational_paper_session_profile_revisions_max_orders_check
        check (max_orders between 1 and 1000000),
    constraint operational_paper_session_profile_revisions_max_events_check
        check (max_events between 1 and 20000000),
    constraint op_ps_profile_revisions_window_relationship_check
        check (warmup_candles <= history_window and history_window <= max_candles),
    constraint op_ps_profile_revisions_warmup_lifecycle_check
        check (warmup_candles = 0 or strategy_lifecycle_version = 2),
    constraint op_ps_profile_revisions_engine_version_check
        check (engine_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    constraint operational_paper_session_profile_revisions_market_regime_check
        check (
            market_regime_policy is null
            or jsonb_typeof(market_regime_policy) = 'object'
        )
);


alter table public.operational_paper_session_profiles
    add constraint operational_paper_session_profiles_current_revision_fkey
    foreign key (profile_id, current_revision)
    references public.operational_paper_session_profile_revisions (profile_id, revision)
    on delete restrict;

alter table public.operational_paper_session_profiles
    add constraint operational_paper_session_profiles_approved_revision_fkey
    foreign key (profile_id, approved_revision, approved_checksum)
    references public.operational_paper_session_profile_revisions (
        profile_id,
        revision,
        specification_checksum
    )
    on delete restrict;


create index operational_paper_session_profiles_state_created_idx
    on public.operational_paper_session_profiles (
        state,
        created_at desc,
        profile_id desc
    );

create index operational_paper_session_profiles_created_idx
    on public.operational_paper_session_profiles (
        created_at desc,
        profile_id desc
    );


create function public.validate_operational_paper_session_profile_revision_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    aggregate_state text;
    aggregate_revision bigint;
begin
    select profile.state, profile.current_revision
    into aggregate_state, aggregate_revision
    from public.operational_paper_session_profiles as profile
    where profile.profile_id = new.profile_id
    for update;

    if not found then
        if new.revision <> 1 then
            raise exception using
                errcode = '23514',
                message = 'operational_paper_session_profile_initial_revision_invalid';
        end if;

        return new;
    end if;

    if aggregate_state <> 'DRAFT' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_profile_revision_append_forbidden';
    end if;

    if new.revision <> aggregate_revision + 1 then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_profile_revision_sequence_invalid';
    end if;

    return new;
end;
$function$;

create trigger operational_paper_session_profile_revisions_validate_insert
before insert on public.operational_paper_session_profile_revisions
for each row
execute function public.validate_operational_paper_session_profile_revision_insert();


create function public.validate_operational_paper_session_profile_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    revision_actor uuid;
begin
    if new.state <> 'DRAFT'
        or new.current_revision <> 1
        or new.record_version <> 1
        or new.approved_revision is not null
        or new.approved_checksum is not null
        or new.approved_by is not null
        or new.approved_at is not null
        or new.archived_by is not null
        or new.archived_at is not null
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_profile_initial_state_invalid';
    end if;

    select revision.created_by
    into revision_actor
    from public.operational_paper_session_profile_revisions as revision
    where revision.profile_id = new.profile_id
      and revision.revision = 1;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_profile_initial_revision_missing';
    end if;

    if revision_actor is distinct from new.created_by then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_profile_initial_actor_mismatch';
    end if;

    return new;
end;
$function$;

create trigger operational_paper_session_profiles_validate_insert
before insert on public.operational_paper_session_profiles
for each row
execute function public.validate_operational_paper_session_profile_insert();


create function public.ensure_operational_paper_session_profile_revision_published()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    published_revision bigint;
begin
    select profile.current_revision
    into published_revision
    from public.operational_paper_session_profiles as profile
    where profile.profile_id = new.profile_id;

    if not found or published_revision < new.revision then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_profile_revision_not_published';
    end if;

    return null;
end;
$function$;

create constraint trigger operational_paper_session_profile_revision_publication_check
after insert on public.operational_paper_session_profile_revisions
deferrable initially deferred
for each row
execute function public.ensure_operational_paper_session_profile_revision_published();


create function public.protect_operational_paper_session_profile()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_profile_delete_forbidden';
    end if;

    if new.profile_id is distinct from old.profile_id
        or new.created_by is distinct from old.created_by
        or new.created_at is distinct from old.created_at
        or new.create_idempotency_key is distinct from old.create_idempotency_key
        or new.create_intent_fingerprint is distinct from old.create_intent_fingerprint
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_profile_identity_immutable';
    end if;

    if new.record_version is distinct from old.record_version + 1 then
        raise exception using
            errcode = '40001',
            message = 'operational_paper_session_profile_record_version_conflict';
    end if;

    if old.state = 'ARCHIVED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_profile_terminal';
    end if;

    if old.state = 'DRAFT' and new.state = 'DRAFT' then
        if new.current_revision is distinct from old.current_revision + 1
            or new.approved_revision is not null
            or new.approved_checksum is not null
            or new.approved_by is not null
            or new.approved_at is not null
            or new.archived_by is not null
            or new.archived_at is not null
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_profile_revision_publication_invalid';
        end if;

        if not exists (
            select 1
            from public.operational_paper_session_profile_revisions as revision
            where revision.profile_id = new.profile_id
              and revision.revision = new.current_revision
        ) then
            raise exception using
                errcode = '23503',
                message = 'operational_paper_session_profile_revision_missing';
        end if;

        return new;
    end if;

    if old.state = 'DRAFT' and new.state = 'APPROVED' then
        if new.current_revision is distinct from old.current_revision
            or new.approved_revision is distinct from new.current_revision
            or new.approved_checksum is null
            or new.approved_by is null
            or new.approved_at is null
            or new.archived_by is not null
            or new.archived_at is not null
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_profile_approval_invalid';
        end if;

        return new;
    end if;

    if old.state = 'DRAFT' and new.state = 'ARCHIVED' then
        if new.current_revision is distinct from old.current_revision
            or new.approved_revision is not null
            or new.approved_checksum is not null
            or new.approved_by is not null
            or new.approved_at is not null
            or new.archived_by is null
            or new.archived_at is null
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_profile_draft_archive_invalid';
        end if;

        return new;
    end if;

    if old.state = 'APPROVED' and new.state = 'ARCHIVED' then
        if new.current_revision is distinct from old.current_revision
            or new.approved_revision is distinct from old.approved_revision
            or new.approved_checksum is distinct from old.approved_checksum
            or new.approved_by is distinct from old.approved_by
            or new.approved_at is distinct from old.approved_at
            or new.archived_by is null
            or new.archived_at is null
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_profile_approved_archive_invalid';
        end if;

        return new;
    end if;

    raise exception using
        errcode = '55000',
        message = 'operational_paper_session_profile_transition_invalid';
end;
$function$;

create trigger operational_paper_session_profiles_protect_update_delete
before update or delete on public.operational_paper_session_profiles
for each row
execute function public.protect_operational_paper_session_profile();


create function public.reject_operational_paper_session_profile_revision_change()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception using
        errcode = '55000',
        message = 'operational_paper_session_profile_revision_immutable';
end;
$function$;

create trigger op_ps_profile_revisions_reject_update_delete
before update or delete on public.operational_paper_session_profile_revisions
for each row
execute function public.reject_operational_paper_session_profile_revision_change();


alter table public.operational_paper_session_profiles enable row level security;
alter table public.operational_paper_session_profile_revisions enable row level security;

-- No RLS policies are created. Only the direct backend owner connection may
-- access operational paper-session profile persistence.
revoke all privileges
    on table
        public.operational_paper_session_profiles,
        public.operational_paper_session_profile_revisions
    from public, anon, authenticated, service_role;

revoke all privileges
    on function
        public.validate_operational_paper_session_profile_revision_insert(),
        public.validate_operational_paper_session_profile_insert(),
        public.ensure_operational_paper_session_profile_revision_published(),
        public.protect_operational_paper_session_profile(),
        public.reject_operational_paper_session_profile_revision_change()
    from public, anon, authenticated, service_role;
