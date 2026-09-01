-- ADT Phase 7-09: backend-only operational paper-session materialization authority.
--
-- This table records durable authorization/provenance for publishing one exact
-- immutable local PaperSessionConfig. It does not replace the Phase 1 capital
-- ledger, the 7-08 authorization authority, or the local paper repository.

create table public.operational_paper_session_materializations (
    materialization_id uuid primary key default gen_random_uuid(),
    schema_version integer not null,
    materialization_contract_version integer not null,
    state text not null,
    record_version bigint not null,
    authorization_id uuid not null,
    authorization_checksum text not null,
    profile_id uuid not null,
    profile_approved_revision bigint not null,
    profile_specification_checksum text not null,
    mandate_id uuid not null,
    mandate_approved_revision bigint not null,
    mandate_specification_checksum text not null,
    simulation_id uuid not null,
    config_checksum text not null,
    session_id text not null,
    materialization_checksum text not null,
    prepared_by uuid not null,
    prepared_at timestamptz not null,
    materialized_by uuid,
    materialized_at timestamptz,

    constraint op_ps_mat_authorization_fkey
        foreign key (authorization_id)
        references public.operational_paper_capital_authorizations (authorization_id)
        on delete restrict,

    constraint op_ps_mat_authorization_key
        unique (authorization_id),

    constraint op_ps_mat_profile_revision_fkey
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

    constraint op_ps_mat_mandate_revision_fkey
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

    constraint op_ps_mat_simulation_fkey
        foreign key (simulation_id)
        references public.simulation_runs (id)
        on delete restrict,

    constraint op_ps_mat_prepared_by_fkey
        foreign key (prepared_by)
        references auth.users (id)
        on delete restrict,

    constraint op_ps_mat_materialized_by_fkey
        foreign key (materialized_by)
        references auth.users (id)
        on delete restrict,

    constraint op_ps_mat_schema_version_check
        check (schema_version = 1),

    constraint op_ps_mat_contract_version_check
        check (materialization_contract_version = 1),

    constraint op_ps_mat_state_check
        check (state in ('PREPARED', 'MATERIALIZED')),

    constraint op_ps_mat_record_version_check
        check (record_version >= 1),

    constraint op_ps_mat_profile_revision_check
        check (profile_approved_revision >= 1),

    constraint op_ps_mat_mandate_revision_check
        check (mandate_approved_revision >= 1),

    constraint op_ps_mat_authorization_checksum_check
        check (authorization_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_mat_profile_checksum_check
        check (profile_specification_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_mat_mandate_checksum_check
        check (mandate_specification_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_mat_config_checksum_check
        check (config_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_mat_session_id_check
        check (session_id ~ '^[0-9a-f]{64}$'),

    constraint op_ps_mat_materialization_checksum_check
        check (materialization_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_mat_materialized_collective_check
        check (
            (materialized_by is null and materialized_at is null)
            or (materialized_by is not null and materialized_at is not null)
        ),

    constraint op_ps_mat_state_shape_check
        check (
            (
                state = 'PREPARED'
                and record_version = 1
                and materialized_by is null
                and materialized_at is null
            )
            or (
                state = 'MATERIALIZED'
                and record_version = 2
                and materialized_by is not null
                and materialized_at is not null
            )
        ),

    constraint op_ps_mat_chronology_check
        check (materialized_at is null or materialized_at >= prepared_at)
);

comment on table public.operational_paper_session_materializations is
    'Durable provenance and lifecycle authority for exact immutable local paper-session configuration publication.';

create index op_ps_mat_session_idx
    on public.operational_paper_session_materializations (session_id);

create index op_ps_mat_prepared_idx
    on public.operational_paper_session_materializations (
        prepared_at,
        materialization_id
    )
    where state = 'PREPARED';

create index op_ps_mat_list_idx
    on public.operational_paper_session_materializations (
        prepared_at desc,
        materialization_id desc
    );

-- B1A-END


create function public.validate_operational_paper_session_materialization_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    simulation_status text;
    authorization_state text;
    stored_authorization_checksum text;
    authorization_profile_id uuid;
    authorization_profile_revision bigint;
    authorization_profile_checksum text;
    authorization_simulation_id uuid;
    authorization_created_at timestamptz;
    profile_state text;
    profile_current_revision bigint;
    profile_approved_revision bigint;
    profile_approved_checksum text;
    profile_approved_at timestamptz;
    mandate_state text;
    mandate_current_revision bigint;
    mandate_approved_revision bigint;
    mandate_approved_checksum text;
    mandate_approved_at timestamptz;
    revision_mandate_id uuid;
    revision_mandate_revision bigint;
    revision_mandate_checksum text;
begin
    if new.state <> 'PREPARED'
        or new.record_version <> 1
        or new.materialized_by is not null
        or new.materialized_at is not null
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_initial_state_invalid';
    end if;

    -- Canonical lock order starts at the Phase 1 simulation mutex.
    select simulation.status
    into simulation_status
    from public.simulation_runs as simulation
    where simulation.id = new.simulation_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_materialization_simulation_missing';
    end if;

    if simulation_status <> 'ACTIVE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_materialization_simulation_not_active';
    end if;

    select
        capital_authorization.state,
        capital_authorization.authorization_checksum,
        capital_authorization.profile_id,
        capital_authorization.profile_approved_revision,
        capital_authorization.profile_specification_checksum,
        capital_authorization.simulation_id,
        capital_authorization.created_at
    into
        authorization_state,
        stored_authorization_checksum,
        authorization_profile_id,
        authorization_profile_revision,
        authorization_profile_checksum,
        authorization_simulation_id,
        authorization_created_at
    from public.operational_paper_capital_authorizations as capital_authorization
    where capital_authorization.authorization_id = new.authorization_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_materialization_authorization_missing';
    end if;

    if authorization_state <> 'AUTHORIZED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_materialization_authorization_not_authorized';
    end if;

    if stored_authorization_checksum is distinct from new.authorization_checksum then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_authorization_checksum_mismatch';
    end if;

    if authorization_profile_id is distinct from new.profile_id
        or authorization_profile_revision is distinct from new.profile_approved_revision
        or authorization_profile_checksum is distinct from new.profile_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_authorization_profile_binding_mismatch';
    end if;

    if authorization_simulation_id is distinct from new.simulation_id then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_authorization_simulation_mismatch';
    end if;

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
            message = 'operational_paper_session_materialization_profile_missing';
    end if;

    if profile_state <> 'APPROVED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_materialization_profile_not_approved';
    end if;

    if profile_current_revision is distinct from new.profile_approved_revision
        or profile_approved_revision is distinct from new.profile_approved_revision
        or profile_approved_checksum is distinct from new.profile_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_profile_binding_mismatch';
    end if;

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
            message = 'operational_paper_session_materialization_mandate_missing';
    end if;

    if mandate_state <> 'APPROVED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_materialization_mandate_not_approved';
    end if;

    if mandate_current_revision is distinct from new.mandate_approved_revision
        or mandate_approved_revision is distinct from new.mandate_approved_revision
        or mandate_approved_checksum is distinct from new.mandate_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_mandate_binding_mismatch';
    end if;

    select
        revision.mandate_id,
        revision.mandate_approved_revision,
        revision.mandate_specification_checksum
    into
        revision_mandate_id,
        revision_mandate_revision,
        revision_mandate_checksum
    from public.operational_paper_session_profile_revisions as revision
    where revision.profile_id = new.profile_id
      and revision.revision = new.profile_approved_revision
      and revision.specification_checksum = new.profile_specification_checksum;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_materialization_profile_revision_missing';
    end if;

    if revision_mandate_id is distinct from new.mandate_id
        or revision_mandate_revision is distinct from new.mandate_approved_revision
        or revision_mandate_checksum is distinct from new.mandate_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_profile_mandate_binding_mismatch';
    end if;

    if new.prepared_at < authorization_created_at
        or new.prepared_at < profile_approved_at
        or new.prepared_at < mandate_approved_at
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_prepared_at_invalid';
    end if;

    return new;
end;
$function$;

comment on function public.validate_operational_paper_session_materialization_insert() is
    'Linearizes PREPARED creation against the ACTIVE simulation, AUTHORIZED capital authorization, APPROVED profile and APPROVED mandate under canonical row locks.';

create trigger operational_paper_session_materializations_validate_insert
before insert on public.operational_paper_session_materializations
for each row
execute function public.validate_operational_paper_session_materialization_insert();

-- B1B-END


create function public.protect_operational_paper_session_materialization()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_materialization_delete_forbidden';
    end if;

    if new.materialization_id is distinct from old.materialization_id
        or new.schema_version is distinct from old.schema_version
        or new.materialization_contract_version is distinct from old.materialization_contract_version
        or new.authorization_id is distinct from old.authorization_id
        or new.authorization_checksum is distinct from old.authorization_checksum
        or new.profile_id is distinct from old.profile_id
        or new.profile_approved_revision is distinct from old.profile_approved_revision
        or new.profile_specification_checksum is distinct from old.profile_specification_checksum
        or new.mandate_id is distinct from old.mandate_id
        or new.mandate_approved_revision is distinct from old.mandate_approved_revision
        or new.mandate_specification_checksum is distinct from old.mandate_specification_checksum
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_materialization_immutable_fields_changed';
    end if;

    if new.simulation_id is distinct from old.simulation_id
        or new.config_checksum is distinct from old.config_checksum
        or new.session_id is distinct from old.session_id
        or new.materialization_checksum is distinct from old.materialization_checksum
        or new.prepared_by is distinct from old.prepared_by
        or new.prepared_at is distinct from old.prepared_at
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_materialization_immutable_fields_changed';
    end if;

    if new.record_version is distinct from old.record_version + 1 then
        raise exception using
            errcode = '40001',
            message = 'operational_paper_session_materialization_record_version_conflict';
    end if;

    if old.state = 'MATERIALIZED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_materialization_terminal';
    end if;

    if old.state <> 'PREPARED' or new.state <> 'MATERIALIZED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_materialization_transition_forbidden';
    end if;

    if new.materialized_by is null or new.materialized_at is null then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_materialized_metadata_required';
    end if;

    if new.materialized_at < old.prepared_at then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_materialization_materialized_at_invalid';
    end if;

    return new;
end;
$function$;

comment on function public.protect_operational_paper_session_materialization() is
    'Preserves immutable materialization provenance and permits only PREPARED-to-MATERIALIZED with exact record-version advancement.';

create trigger operational_paper_session_materializations_protect
before update or delete on public.operational_paper_session_materializations
for each row
execute function public.protect_operational_paper_session_materialization();

-- B1C-END


alter table public.operational_paper_session_materializations
    enable row level security;

-- No RLS policies are created. Operational materialization authority is
-- backend-only through the direct PostgreSQL owner connection.

revoke all privileges
    on table public.operational_paper_session_materializations
    from public, anon, authenticated, service_role;

-- Trigger functions enforce invariants and are not callable application APIs.
revoke all privileges
    on function
        public.validate_operational_paper_session_materialization_insert(),
        public.protect_operational_paper_session_materialization()
    from public, anon, authenticated, service_role;

-- B1D-END
