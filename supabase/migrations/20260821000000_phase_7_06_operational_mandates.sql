-- ADT Phase 7-06: backend-only operational mandate persistence authority.
--
-- Specifications are immutable revisions. The aggregate publishes exactly one
-- current revision and may seal that revision through the one-way lifecycle.

create table public.operational_mandates (
    mandate_id uuid primary key default gen_random_uuid(),
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
    create_request_fingerprint text not null,
    constraint operational_mandates_created_by_fkey
        foreign key (created_by)
        references auth.users (id)
        on delete restrict,
    constraint operational_mandates_approved_by_fkey
        foreign key (approved_by)
        references auth.users (id)
        on delete restrict,
    constraint operational_mandates_archived_by_fkey
        foreign key (archived_by)
        references auth.users (id)
        on delete restrict,
    constraint operational_mandates_actor_idempotency_key
        unique (created_by, create_idempotency_key),
    constraint operational_mandates_state_check
        check (state in ('DRAFT', 'APPROVED', 'ARCHIVED')),
    constraint operational_mandates_revision_check
        check (current_revision >= 1),
    constraint operational_mandates_record_version_check
        check (record_version >= 1),
    constraint operational_mandates_idempotency_key_check
        check (
            create_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(create_idempotency_key) <= 128
        ),
    constraint operational_mandates_fingerprint_check
        check (create_request_fingerprint ~ '^[0-9a-f]{64}$'),
    constraint operational_mandates_approved_checksum_check
        check (
            approved_checksum is null
            or approved_checksum ~ '^[0-9a-f]{64}$'
        ),
    constraint operational_mandates_approval_collective_check
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
    constraint operational_mandates_archive_collective_check
        check (
            (
                archived_by is null
                and archived_at is null
            )
            or (
                archived_by is not null
                and archived_at is not null
            )
        ),
    constraint operational_mandates_state_shape_check
        check (
            (
                state = 'DRAFT'
                and approved_revision is null
                and archived_by is null
            )
            or (
                state = 'APPROVED'
                and approved_revision is not null
                and archived_by is null
            )
            or (
                state = 'ARCHIVED'
                and archived_by is not null
            )
        ),
    constraint operational_mandates_approved_revision_check
        check (
            approved_revision is null
            or approved_revision = current_revision
        ),
    constraint operational_mandates_chronology_check
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


create table public.operational_mandate_revisions (
    mandate_id uuid not null,
    revision bigint not null,
    schema_version integer not null,
    specification_checksum text not null,
    name text not null,
    description text not null,
    created_by uuid not null,
    created_at timestamptz not null,
    constraint operational_mandate_revisions_pkey
        primary key (mandate_id, revision),
    constraint operational_mandate_revisions_checksum_key
        unique (mandate_id, revision, specification_checksum),
    constraint operational_mandate_revisions_mandate_id_fkey
        foreign key (mandate_id)
        references public.operational_mandates (mandate_id)
        on delete restrict
        deferrable initially deferred,
    constraint operational_mandate_revisions_created_by_fkey
        foreign key (created_by)
        references auth.users (id)
        on delete restrict,
    constraint operational_mandate_revisions_revision_check
        check (revision >= 1),
    constraint operational_mandate_revisions_schema_version_check
        check (schema_version = 1),
    constraint operational_mandate_revisions_checksum_check
        check (specification_checksum ~ '^[0-9a-f]{64}$'),
    constraint operational_mandate_revisions_name_check
        check (
            char_length(name) between 1 and 120
            and name = btrim(name, ' ')
            and position(chr(13) in name) = 0
        ),
    constraint operational_mandate_revisions_description_check
        check (
            char_length(description) <= 1000
            and position(chr(13) in description) = 0
        )
);


create table public.operational_mandate_revision_instruments (
    mandate_id uuid not null,
    revision bigint not null,
    exchange text not null,
    market_type text not null,
    base_asset text not null,
    quote_asset text not null,
    constraint operational_mandate_revision_instruments_pkey
        primary key (
            mandate_id,
            revision,
            exchange,
            market_type,
            base_asset,
            quote_asset
        ),
    constraint operational_mandate_revision_instruments_revision_fkey
        foreign key (mandate_id, revision)
        references public.operational_mandate_revisions (mandate_id, revision)
        on delete restrict,
    constraint operational_mandate_revision_instruments_capability_check
        check (
            exchange = 'binance'
            and market_type = 'spot'
        ),
    constraint operational_mandate_revision_instruments_assets_check
        check (
            base_asset ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'
            and quote_asset ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'
            and base_asset <> quote_asset
        )
);


alter table public.operational_mandates
    add constraint operational_mandates_current_revision_fkey
    foreign key (mandate_id, current_revision)
    references public.operational_mandate_revisions (mandate_id, revision)
    on delete restrict;

alter table public.operational_mandates
    add constraint operational_mandates_approved_revision_fkey
    foreign key (
        mandate_id,
        approved_revision,
        approved_checksum
    )
    references public.operational_mandate_revisions (
        mandate_id,
        revision,
        specification_checksum
    )
    on delete restrict;


create index operational_mandates_state_created_idx
    on public.operational_mandates (
        state,
        created_at desc,
        mandate_id desc
    );

create index operational_mandates_created_idx
    on public.operational_mandates (
        created_at desc,
        mandate_id desc
    );


create function public.validate_operational_mandate_revision_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    aggregate_state text;
    aggregate_revision bigint;
begin
    select mandate.state, mandate.current_revision
    into aggregate_state, aggregate_revision
    from public.operational_mandates as mandate
    where mandate.mandate_id = new.mandate_id
    for update;

    if not found then
        if new.revision <> 1 then
            raise exception using
                errcode = '23514',
                message = 'operational_mandate_initial_revision_invalid';
        end if;

        return new;
    end if;

    if aggregate_state <> 'DRAFT' then
        raise exception using
            errcode = '55000',
            message = 'operational_mandate_revision_append_forbidden';
    end if;

    if new.revision <> aggregate_revision + 1 then
        raise exception using
            errcode = '23514',
            message = 'operational_mandate_revision_sequence_invalid';
    end if;

    return new;
end;
$function$;


create trigger operational_mandate_revisions_validate_insert
before insert on public.operational_mandate_revisions
for each row
execute function public.validate_operational_mandate_revision_insert();


create function public.validate_operational_mandate_instrument_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    aggregate_state text;
    aggregate_revision bigint;
    existing_instruments integer;
begin
    perform 1
    from public.operational_mandate_revisions as revision
    where revision.mandate_id = new.mandate_id
      and revision.revision = new.revision
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_mandate_instrument_revision_missing';
    end if;

    select mandate.state, mandate.current_revision
    into aggregate_state, aggregate_revision
    from public.operational_mandates as mandate
    where mandate.mandate_id = new.mandate_id;

    if not found then
        if new.revision <> 1 then
            raise exception using
                errcode = '23514',
                message = 'operational_mandate_initial_instrument_revision_invalid';
        end if;
    elsif aggregate_state <> 'DRAFT'
        or new.revision <> aggregate_revision + 1
    then
        raise exception using
            errcode = '55000',
            message = 'operational_mandate_instrument_append_forbidden';
    end if;

    select count(*)
    into existing_instruments
    from public.operational_mandate_revision_instruments as instrument
    where instrument.mandate_id = new.mandate_id
      and instrument.revision = new.revision;

    if existing_instruments >= 100 then
        raise exception using
            errcode = '23514',
            message = 'operational_mandate_instrument_limit_exceeded';
    end if;

    return new;
end;
$function$;


create trigger operational_mandate_instruments_validate_insert
before insert on public.operational_mandate_revision_instruments
for each row
execute function public.validate_operational_mandate_instrument_insert();


create function public.validate_operational_mandate_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    revision_actor uuid;
    instrument_total bigint;
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
            message = 'operational_mandate_initial_state_invalid';
    end if;

    select revision.created_by, count(instrument.*)
    into revision_actor, instrument_total
    from public.operational_mandate_revisions as revision
    left join public.operational_mandate_revision_instruments as instrument
      on instrument.mandate_id = revision.mandate_id
     and instrument.revision = revision.revision
    where revision.mandate_id = new.mandate_id
      and revision.revision = 1
    group by revision.created_by;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_mandate_initial_revision_missing';
    end if;

    if revision_actor is distinct from new.created_by then
        raise exception using
            errcode = '23514',
            message = 'operational_mandate_initial_actor_mismatch';
    end if;

    if instrument_total < 1 or instrument_total > 100 then
        raise exception using
            errcode = '23514',
            message = 'operational_mandate_initial_instrument_count_invalid';
    end if;

    return new;
end;
$function$;


create trigger operational_mandates_validate_insert
before insert on public.operational_mandates
for each row
execute function public.validate_operational_mandate_insert();


create function public.ensure_operational_mandate_revision_published()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    published_revision bigint;
begin
    select mandate.current_revision
    into published_revision
    from public.operational_mandates as mandate
    where mandate.mandate_id = new.mandate_id;

    if not found or published_revision < new.revision then
        raise exception using
            errcode = '23514',
            message = 'operational_mandate_revision_not_published';
    end if;

    return null;
end;
$function$;


create constraint trigger operational_mandate_revision_publication_check
after insert on public.operational_mandate_revisions
deferrable initially deferred
for each row
execute function public.ensure_operational_mandate_revision_published();


create function public.protect_operational_mandate()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    target_instruments bigint;
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'operational_mandate_delete_forbidden';
    end if;

    if new.mandate_id is distinct from old.mandate_id
        or new.created_by is distinct from old.created_by
        or new.created_at is distinct from old.created_at
        or new.create_idempotency_key is distinct from old.create_idempotency_key
        or new.create_request_fingerprint is distinct from old.create_request_fingerprint
    then
        raise exception using
            errcode = '55000',
            message = 'operational_mandate_identity_immutable';
    end if;

    if new.record_version is distinct from old.record_version + 1 then
        raise exception using
            errcode = '40001',
            message = 'operational_mandate_record_version_conflict';
    end if;

    if old.state = 'ARCHIVED' then
        raise exception using
            errcode = '55000',
            message = 'operational_mandate_terminal';
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
                message = 'operational_mandate_revision_publication_invalid';
        end if;

        select count(*)
        into target_instruments
        from public.operational_mandate_revision_instruments as instrument
        where instrument.mandate_id = new.mandate_id
          and instrument.revision = new.current_revision;

        if target_instruments < 1 or target_instruments > 100 then
            raise exception using
                errcode = '23514',
                message = 'operational_mandate_revision_instrument_count_invalid';
        end if;

        if not exists (
            select 1
            from public.operational_mandate_revisions as revision
            where revision.mandate_id = new.mandate_id
              and revision.revision = new.current_revision
        ) then
            raise exception using
                errcode = '23503',
                message = 'operational_mandate_revision_missing';
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
                message = 'operational_mandate_approval_invalid';
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
                message = 'operational_mandate_draft_archive_invalid';
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
                message = 'operational_mandate_approved_archive_invalid';
        end if;

        return new;
    end if;

    raise exception using
        errcode = '55000',
        message = 'operational_mandate_transition_invalid';
end;
$function$;


create trigger operational_mandates_protect_update_delete
before update or delete on public.operational_mandates
for each row
execute function public.protect_operational_mandate();


create function public.reject_operational_mandate_revision_change()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception using
        errcode = '55000',
        message = 'operational_mandate_revision_immutable';
end;
$function$;


create trigger operational_mandate_revisions_reject_update_delete
before update or delete on public.operational_mandate_revisions
for each row
execute function public.reject_operational_mandate_revision_change();


create function public.reject_operational_mandate_instrument_change()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception using
        errcode = '55000',
        message = 'operational_mandate_instrument_immutable';
end;
$function$;


create trigger operational_mandate_instruments_reject_update_delete
before update or delete on public.operational_mandate_revision_instruments
for each row
execute function public.reject_operational_mandate_instrument_change();


alter table public.operational_mandates enable row level security;
alter table public.operational_mandate_revisions enable row level security;
alter table public.operational_mandate_revision_instruments enable row level security;

-- No RLS policies are created. Only the direct backend owner connection may
-- access operational mandate persistence.
revoke all privileges
    on table
        public.operational_mandates,
        public.operational_mandate_revisions,
        public.operational_mandate_revision_instruments
    from public, anon, authenticated, service_role;

revoke all privileges
    on function
        public.validate_operational_mandate_revision_insert(),
        public.validate_operational_mandate_instrument_insert(),
        public.validate_operational_mandate_insert(),
        public.ensure_operational_mandate_revision_published(),
        public.protect_operational_mandate(),
        public.reject_operational_mandate_revision_change(),
        public.reject_operational_mandate_instrument_change()
    from public, anon, authenticated, service_role;
