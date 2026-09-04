-- ADT Phase 7-10: backend-only operational paper-session activation authority.
--
-- Activation authorizes one exact MATERIALIZED paper-session configuration for
-- future execution eligibility. It does not start or control a runner.

create table public.operational_paper_session_activations (
    activation_id uuid primary key default gen_random_uuid(),
    schema_version integer not null,
    activation_contract_version integer not null,
    state text not null,
    record_version bigint not null,

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
    activation_checksum text not null,

    authorized_by uuid not null,
    authorized_at timestamptz not null,
    revoked_by uuid,
    revoked_at timestamptz,

    create_idempotency_key text not null,
    create_intent_fingerprint text not null,
    constraint op_ps_activation_materialization_fkey
        foreign key (materialization_id)
        references public.operational_paper_session_materializations (materialization_id)
        on delete restrict,

    constraint op_ps_activation_authorization_fkey
        foreign key (authorization_id)
        references public.operational_paper_capital_authorizations (authorization_id)
        on delete restrict,

    constraint op_ps_activation_profile_revision_fkey
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
    constraint op_ps_activation_mandate_revision_fkey
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

    constraint op_ps_activation_simulation_fkey
        foreign key (simulation_id)
        references public.simulation_runs (id)
        on delete restrict,

    constraint op_ps_activation_authorized_by_fkey
        foreign key (authorized_by)
        references auth.users (id)
        on delete restrict,

    constraint op_ps_activation_revoked_by_fkey
        foreign key (revoked_by)
        references auth.users (id)
        on delete restrict,

    constraint op_ps_activation_actor_idempotency_key
        unique (authorized_by, create_idempotency_key),
    constraint op_ps_activation_schema_version_check
        check (schema_version = 1),

    constraint op_ps_activation_contract_version_check
        check (activation_contract_version = 1),

    constraint op_ps_activation_state_check
        check (state in ('AUTHORIZED', 'REVOKED')),

    constraint op_ps_activation_profile_revision_check
        check (profile_approved_revision >= 1),

    constraint op_ps_activation_mandate_revision_check
        check (mandate_approved_revision >= 1),

    constraint op_ps_activation_materialization_checksum_check
        check (materialization_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_activation_authorization_checksum_check
        check (authorization_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_activation_profile_checksum_check
        check (profile_specification_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_activation_mandate_checksum_check
        check (mandate_specification_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_activation_session_id_check
        check (session_id ~ '^[0-9a-f]{64}$'),

    constraint op_ps_activation_config_checksum_check
        check (config_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_activation_checksum_check
        check (activation_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_ps_activation_fingerprint_check
        check (create_intent_fingerprint ~ '^[0-9a-f]{64}$'),

    constraint op_ps_activation_idempotency_key_check
        check (
            create_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(create_idempotency_key) <= 128
        ),

    constraint op_ps_activation_nonzero_activation_id_check
        check (activation_id <> '00000000-0000-0000-0000-000000000000'::uuid),

    constraint op_ps_activation_nonzero_materialization_id_check
        check (materialization_id <> '00000000-0000-0000-0000-000000000000'::uuid),

    constraint op_ps_activation_nonzero_authorization_id_check
        check (authorization_id <> '00000000-0000-0000-0000-000000000000'::uuid),

    constraint op_ps_activation_nonzero_profile_id_check
        check (profile_id <> '00000000-0000-0000-0000-000000000000'::uuid),

    constraint op_ps_activation_nonzero_mandate_id_check
        check (mandate_id <> '00000000-0000-0000-0000-000000000000'::uuid),

    constraint op_ps_activation_nonzero_simulation_id_check
        check (simulation_id <> '00000000-0000-0000-0000-000000000000'::uuid),

    constraint op_ps_activation_nonzero_authorized_by_check
        check (authorized_by <> '00000000-0000-0000-0000-000000000000'::uuid),

    constraint op_ps_activation_nonzero_revoked_by_check
        check (revoked_by is null or revoked_by <> '00000000-0000-0000-0000-000000000000'::uuid),

    constraint op_ps_activation_revocation_collective_check
        check (
            (revoked_by is null and revoked_at is null)
            or (revoked_by is not null and revoked_at is not null)
        ),

    constraint op_ps_activation_state_shape_check
        check (
            (
                state = 'AUTHORIZED'
                and record_version = 1
                and revoked_by is null
                and revoked_at is null
            )
            or (
                state = 'REVOKED'
                and record_version = 2
                and revoked_by is not null
                and revoked_at is not null
            )
        ),

    constraint op_ps_activation_authorized_at_finite_check
        check (isfinite(authorized_at)),

    constraint op_ps_activation_revoked_at_finite_check
        check (revoked_at is null or isfinite(revoked_at)),

    constraint op_ps_activation_chronology_check
        check (revoked_at is null or revoked_at >= authorized_at)
);

comment on table public.operational_paper_session_activations is
    'Durable historical execution-activation grants for exact materialized paper sessions.';

create unique index op_ps_activation_one_authorized_per_materialization_uidx
    on public.operational_paper_session_activations (materialization_id)
    where state = 'AUTHORIZED';

create index op_ps_activation_materialization_history_idx
    on public.operational_paper_session_activations (
        materialization_id,
        authorized_at desc,
        activation_id desc
    );

create index op_ps_activation_list_idx
    on public.operational_paper_session_activations (
        authorized_at desc,
        activation_id desc
    );

create function public.validate_operational_paper_session_activation_insert()
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
    revision_quote_asset text;

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
begin
    if new.state <> 'AUTHORIZED'
        or new.record_version <> 1
        or new.revoked_by is not null
        or new.revoked_at is not null
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_initial_state_invalid';
    end if;

    select simulation.status, simulation.currency
    into simulation_status, simulation_currency
    from public.simulation_runs as simulation
    where simulation.id = new.simulation_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_activation_simulation_missing';
    end if;

    if simulation_status <> 'ACTIVE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_activation_simulation_not_active';
    end if;

    select
        capital_authorization.state,
        capital_authorization.authorization_checksum,
        capital_authorization.profile_id,
        capital_authorization.profile_approved_revision,
        capital_authorization.profile_specification_checksum,
        capital_authorization.simulation_id,
        capital_authorization.quote_asset,
        capital_authorization.created_at
    into
        authorization_state,
        stored_authorization_checksum,
        authorization_profile_id,
        authorization_profile_revision,
        authorization_profile_checksum,
        authorization_simulation_id,
        authorization_quote_asset,
        authorization_created_at
    from public.operational_paper_capital_authorizations as capital_authorization
    where capital_authorization.authorization_id = new.authorization_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_session_activation_authorization_missing';
    end if;

    if authorization_state <> 'AUTHORIZED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_activation_authorization_not_authorized';
    end if;

    if stored_authorization_checksum is distinct from new.authorization_checksum then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_authorization_checksum_mismatch';
    end if;

    if authorization_profile_id is distinct from new.profile_id
        or authorization_profile_revision is distinct from new.profile_approved_revision
        or authorization_profile_checksum is distinct from new.profile_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_authorization_profile_binding_mismatch';
    end if;

    if authorization_simulation_id is distinct from new.simulation_id then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_authorization_simulation_mismatch';
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
            message = 'operational_paper_session_activation_profile_missing';
    end if;

    if profile_state <> 'APPROVED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_activation_profile_not_approved';
    end if;

    if profile_current_revision is distinct from new.profile_approved_revision
        or profile_approved_revision is distinct from new.profile_approved_revision
        or profile_approved_checksum is distinct from new.profile_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_profile_binding_mismatch';
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
            message = 'operational_paper_session_activation_mandate_missing';
    end if;

    if mandate_state <> 'APPROVED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_activation_mandate_not_approved';
    end if;

    if mandate_current_revision is distinct from new.mandate_approved_revision
        or mandate_approved_revision is distinct from new.mandate_approved_revision
        or mandate_approved_checksum is distinct from new.mandate_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_mandate_binding_mismatch';
    end if;

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
            message = 'operational_paper_session_activation_materialization_missing';
    end if;

    if materialization_state <> 'MATERIALIZED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_activation_materialization_not_materialized';
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
            message = 'operational_paper_session_activation_profile_revision_missing';
    end if;

    if revision_mandate_id is distinct from new.mandate_id
        or revision_mandate_revision is distinct from new.mandate_approved_revision
        or revision_mandate_checksum is distinct from new.mandate_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_profile_mandate_binding_mismatch';
    end if;

    if authorization_quote_asset is distinct from revision_quote_asset then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_authorization_quote_asset_mismatch';
    end if;

    if simulation_currency is distinct from revision_quote_asset then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_currency_mismatch';
    end if;

    if stored_materialization_checksum is distinct from new.materialization_checksum then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_materialization_checksum_mismatch';
    end if;

    if materialization_authorization_id is distinct from new.authorization_id
        or materialization_authorization_checksum is distinct from new.authorization_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_materialization_authorization_binding_mismatch';
    end if;

    if materialization_profile_id is distinct from new.profile_id
        or materialization_profile_revision is distinct from new.profile_approved_revision
        or materialization_profile_checksum is distinct from new.profile_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_materialization_profile_binding_mismatch';
    end if;

    if materialization_mandate_id is distinct from new.mandate_id
        or materialization_mandate_revision is distinct from new.mandate_approved_revision
        or materialization_mandate_checksum is distinct from new.mandate_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_materialization_mandate_binding_mismatch';
    end if;

    if materialization_simulation_id is distinct from new.simulation_id then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_materialization_simulation_mismatch';
    end if;

    if materialization_session_id is distinct from new.session_id
        or materialization_config_checksum is distinct from new.config_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_materialization_config_binding_mismatch';
    end if;

    if materialized_at is null
        or new.authorized_at < materialized_at
        or new.authorized_at < authorization_created_at
        or new.authorized_at < profile_approved_at
        or new.authorized_at < mandate_approved_at
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_authorized_at_invalid';
    end if;

    return new;
end;
$function$;

comment on function public.validate_operational_paper_session_activation_insert() is
    'Linearizes activation authorization against the ACTIVE simulation, AUTHORIZED capital authorization, APPROVED profile, APPROVED mandate and exact MATERIALIZED paper session.';

create trigger operational_paper_session_activations_validate_insert
before insert on public.operational_paper_session_activations
for each row
execute function public.validate_operational_paper_session_activation_insert();

create function public.protect_operational_paper_session_activation()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_activation_delete_forbidden';
    end if;

    if new.activation_id is distinct from old.activation_id
        or new.schema_version is distinct from old.schema_version
        or new.activation_contract_version is distinct from old.activation_contract_version
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
        or new.activation_checksum is distinct from old.activation_checksum
        or new.authorized_by is distinct from old.authorized_by
        or new.authorized_at is distinct from old.authorized_at
        or new.create_idempotency_key is distinct from old.create_idempotency_key
        or new.create_intent_fingerprint is distinct from old.create_intent_fingerprint
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_activation_immutable_fields_changed';
    end if;

    if new.record_version is distinct from old.record_version + 1 then
        raise exception using
            errcode = '40001',
            message = 'operational_paper_session_activation_record_version_conflict';
    end if;

    if old.state = 'REVOKED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_activation_terminal';
    end if;

    if old.state <> 'AUTHORIZED' or new.state <> 'REVOKED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_activation_transition_forbidden';
    end if;

    if new.revoked_by is null or new.revoked_at is null then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_revocation_metadata_required';
    end if;

    if new.revoked_at < old.authorized_at then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_session_activation_revoked_at_invalid';
    end if;

    return new;
end;
$function$;

comment on function public.protect_operational_paper_session_activation() is
    'Preserves immutable activation-grant provenance and permits only AUTHORIZED-to-REVOKED with exact record-version advancement.';

create trigger operational_paper_session_activations_protect
before update or delete on public.operational_paper_session_activations
for each row
execute function public.protect_operational_paper_session_activation();

alter table public.operational_paper_session_activations
    enable row level security;

-- No RLS policies are created. Activation authority is backend-only through
-- the direct PostgreSQL owner connection.

revoke all privileges
    on table public.operational_paper_session_activations
    from public, anon, authenticated, service_role;

revoke all privileges
    on function
        public.validate_operational_paper_session_activation_insert(),
        public.protect_operational_paper_session_activation()
    from public, anon, authenticated, service_role;
