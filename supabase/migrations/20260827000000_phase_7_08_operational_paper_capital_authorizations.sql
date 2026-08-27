-- ADT Phase 7-08: backend-only operational paper-capital authorization authority.
--
-- The Phase 1 capital ledger remains the gross simulated-capital authority.
-- This table records reservations against that ledger; creating a reservation
-- does not create a capital_movement and does not materialize a paper session.

create table public.operational_paper_capital_authorizations (
    authorization_id uuid primary key default gen_random_uuid(),
    schema_version integer not null,
    state text not null,
    record_version bigint not null,
    profile_id uuid not null,
    profile_approved_revision bigint not null,
    profile_specification_checksum text not null,
    simulation_id uuid not null,
    quote_asset text not null,
    authorized_capital numeric(20, 8) not null,
    authorization_checksum text not null,
    created_by uuid not null,
    created_at timestamptz not null,
    revoked_by uuid,
    revoked_at timestamptz,
    create_idempotency_key text not null,
    create_intent_fingerprint text not null,

    constraint op_pc_auth_profile_revision_fkey
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

    constraint op_pc_auth_simulation_fkey
        foreign key (simulation_id)
        references public.simulation_runs (id)
        on delete restrict,

    constraint op_pc_auth_created_by_fkey
        foreign key (created_by)
        references auth.users (id)
        on delete restrict,

    constraint op_pc_auth_revoked_by_fkey
        foreign key (revoked_by)
        references auth.users (id)
        on delete restrict,

    constraint op_pc_auth_actor_idempotency_key
        unique (created_by, create_idempotency_key),

    constraint op_pc_auth_schema_version_check
        check (schema_version = 1),

    constraint op_pc_auth_state_check
        check (state in ('AUTHORIZED', 'REVOKED')),

    constraint op_pc_auth_record_version_check
        check (record_version >= 1),

    constraint op_pc_auth_profile_revision_check
        check (profile_approved_revision >= 1),

    constraint op_pc_auth_profile_checksum_check
        check (profile_specification_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_pc_auth_quote_asset_check
        check (quote_asset ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'),

    constraint op_pc_auth_capital_check
        check (
            authorized_capital not in (
                'NaN'::numeric,
                'Infinity'::numeric,
                '-Infinity'::numeric
            )
            and authorized_capital > 0
        ),

    constraint op_pc_auth_checksum_check
        check (authorization_checksum ~ '^[0-9a-f]{64}$'),

    constraint op_pc_auth_idempotency_key_check
        check (
            create_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
            and char_length(create_idempotency_key) <= 128
        ),

    constraint op_pc_auth_fingerprint_check
        check (create_intent_fingerprint ~ '^[0-9a-f]{64}$'),

    constraint op_pc_auth_revocation_collective_check
        check (
            (revoked_by is null and revoked_at is null)
            or (revoked_by is not null and revoked_at is not null)
        ),

    constraint op_pc_auth_state_shape_check
        check (
            (state = 'AUTHORIZED' and revoked_by is null and revoked_at is null)
            or (
                state = 'REVOKED'
                and revoked_by is not null
                and revoked_at is not null
            )
        ),

    constraint op_pc_auth_chronology_check
        check (revoked_at is null or revoked_at >= created_at)
);

comment on table public.operational_paper_capital_authorizations is
    'Durable reservations of Phase 1 simulated capital for exact approved paper-session profiles.';

create unique index op_pc_auth_one_active_per_profile_uidx
    on public.operational_paper_capital_authorizations (profile_id)
    where state = 'AUTHORIZED';

create index op_pc_auth_active_simulation_idx
    on public.operational_paper_capital_authorizations (simulation_id)
    where state = 'AUTHORIZED';

create index op_pc_auth_created_idx
    on public.operational_paper_capital_authorizations (
        created_at desc,
        authorization_id desc
    );

-- B2A-1-END


create function public.validate_operational_paper_capital_authorization_insert()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    simulation_status text;
    simulation_currency text;
    profile_state text;
    profile_current_revision bigint;
    profile_approved_revision bigint;
    profile_approved_checksum text;
    profile_quote_asset text;
    gross_balance numeric;
    reserved_capital numeric;
    available_capital numeric;
begin
    if new.state <> 'AUTHORIZED'
        or new.record_version <> 1
        or new.revoked_by is not null
        or new.revoked_at is not null
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_capital_authorization_initial_state_invalid';
    end if;

    -- The simulation row is the single financial mutex shared with the
    -- existing Phase 1 capital ledger.
    select simulation.status, simulation.currency
    into simulation_status, simulation_currency
    from public.simulation_runs as simulation
    where simulation.id = new.simulation_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_capital_authorization_simulation_missing';
    end if;

    if simulation_status <> 'ACTIVE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_capital_authorization_simulation_not_active';
    end if;

    -- Lock the aggregate so approval cannot be archived concurrently with
    -- creation after the exact binding has been validated.
    select
        profile.state,
        profile.current_revision,
        profile.approved_revision,
        profile.approved_checksum
    into
        profile_state,
        profile_current_revision,
        profile_approved_revision,
        profile_approved_checksum
    from public.operational_paper_session_profiles as profile
    where profile.profile_id = new.profile_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_capital_authorization_profile_missing';
    end if;

    if profile_state <> 'APPROVED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_capital_authorization_profile_not_approved';
    end if;

    if profile_current_revision is distinct from new.profile_approved_revision
        or profile_approved_revision is distinct from new.profile_approved_revision
        or profile_approved_checksum is distinct from new.profile_specification_checksum
    then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_capital_authorization_profile_binding_mismatch';
    end if;

    select revision.quote_asset
    into profile_quote_asset
    from public.operational_paper_session_profile_revisions as revision
    where revision.profile_id = new.profile_id
      and revision.revision = new.profile_approved_revision
      and revision.specification_checksum = new.profile_specification_checksum;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'operational_paper_capital_authorization_profile_revision_missing';
    end if;

    if new.quote_asset is distinct from profile_quote_asset then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_capital_authorization_quote_asset_mismatch';
    end if;

    if simulation_currency is distinct from profile_quote_asset then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_capital_authorization_currency_mismatch';
    end if;

    select coalesce(sum(movement.amount), 0::numeric)
    into gross_balance
    from public.capital_movements as movement
    where movement.simulation_id = new.simulation_id;

    select coalesce(sum(capital_authorization.authorized_capital), 0::numeric)
    into reserved_capital
    from public.operational_paper_capital_authorizations as capital_authorization
    where capital_authorization.simulation_id = new.simulation_id
      and capital_authorization.state = 'AUTHORIZED';

    available_capital := gross_balance - reserved_capital;

    if available_capital < new.authorized_capital then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_capital_authorization_insufficient_available_capital';
    end if;

    return new;
end;
$function$;

comment on function public.validate_operational_paper_capital_authorization_insert() is
    'Validates exact approved profile binding and reserves available Phase 1 simulated capital under the simulation row lock.';

create trigger operational_paper_capital_authorizations_validate_insert
before insert on public.operational_paper_capital_authorizations
for each row
execute function public.validate_operational_paper_capital_authorization_insert();

-- B2A-2-END


-- Revocation is monotonic: it can only release reserved capital.
-- This row-level trigger intentionally does not lock simulation_runs.
-- UPDATE already locks the authorization row first; acquiring the simulation
-- lock here would invert the canonical financial lock order and could deadlock
-- against concurrent creation. Gate 2C repository revocation will acquire the
-- simulation row before issuing this UPDATE.
create function public.protect_operational_paper_capital_authorization()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_capital_authorization_delete_forbidden';
    end if;

    if new.authorization_id is distinct from old.authorization_id
        or new.schema_version is distinct from old.schema_version
        or new.profile_id is distinct from old.profile_id
        or new.profile_approved_revision is distinct from old.profile_approved_revision
        or new.profile_specification_checksum is distinct from old.profile_specification_checksum
        or new.simulation_id is distinct from old.simulation_id
        or new.quote_asset is distinct from old.quote_asset
        or new.authorized_capital is distinct from old.authorized_capital
        or new.authorization_checksum is distinct from old.authorization_checksum
        or new.created_by is distinct from old.created_by
        or new.created_at is distinct from old.created_at
        or new.create_idempotency_key is distinct from old.create_idempotency_key
        or new.create_intent_fingerprint is distinct from old.create_intent_fingerprint
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_capital_authorization_immutable_fields_changed';
    end if;

    if new.record_version is distinct from old.record_version + 1 then
        raise exception using
            errcode = '40001',
            message = 'operational_paper_capital_authorization_record_version_conflict';
    end if;

    if old.state = 'REVOKED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_capital_authorization_terminal';
    end if;

    if old.state <> 'AUTHORIZED' or new.state <> 'REVOKED' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_capital_authorization_transition_forbidden';
    end if;

    if new.revoked_by is null or new.revoked_at is null then
        raise exception using
            errcode = '23514',
            message = 'operational_paper_capital_authorization_revocation_metadata_required';
    end if;

    return new;
end;
$function$;

comment on function public.protect_operational_paper_capital_authorization() is
    'Preserves immutable authorization identity and permits only the one-way AUTHORIZED-to-REVOKED lifecycle with exact record-version advancement.';

create trigger operational_paper_capital_authorizations_protect
before update or delete on public.operational_paper_capital_authorizations
for each row
execute function public.protect_operational_paper_capital_authorization();

-- B2A-3-END


-- Extend the Phase 1 ledger validator without replacing its ledger model.
-- Every movement still serializes on simulation_runs; while that same lock
-- is held, the resulting gross balance must also cover every AUTHORIZED
-- operational paper-capital reservation bound to the simulation.
create or replace function public.validate_capital_movement()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    simulation_initial_capital numeric;
    current_balance numeric;
    active_reserved_capital numeric;
    resulting_balance numeric;
    has_any_movement boolean;
    has_initial_capital boolean;
begin
    select simulation.initial_capital
    into simulation_initial_capital
    from public.simulation_runs as simulation
    where simulation.id = new.simulation_id
    for update;

    if not found then
        raise exception using
            errcode = '23503',
            message = 'The capital movement references a simulation that does not exist.';
    end if;

    select
        coalesce(sum(movement.amount), 0::numeric),
        count(*) > 0,
        count(*) filter (where movement.type = 'INITIAL_CAPITAL') > 0
    into current_balance, has_any_movement, has_initial_capital
    from public.capital_movements as movement
    where movement.simulation_id = new.simulation_id;

    if new.type = 'INITIAL_CAPITAL' then
        if has_any_movement then
            raise exception using
                errcode = '23514',
                message = 'INITIAL_CAPITAL must be the first and only opening movement.';
        end if;

        if new.amount <> simulation_initial_capital then
            raise exception using
                errcode = '23514',
                message = 'INITIAL_CAPITAL must equal the simulation initial_capital.';
        end if;
    elsif not has_initial_capital then
        raise exception using
            errcode = '23514',
            message = 'INITIAL_CAPITAL must be recorded before any other movement.';
    end if;

    resulting_balance := current_balance + new.amount;

    if resulting_balance < 0::numeric then
        raise exception using
            errcode = '23514',
            message = 'The capital movement would make the simulation balance negative.';
    end if;

    select coalesce(sum(capital_authorization.authorized_capital), 0::numeric)
    into active_reserved_capital
    from public.operational_paper_capital_authorizations as capital_authorization
    where capital_authorization.simulation_id = new.simulation_id
      and capital_authorization.state = 'AUTHORIZED';

    if resulting_balance < active_reserved_capital then
        raise exception using
            errcode = '23514',
            message = 'capital_movement_would_violate_authorized_reservations';
    end if;

    return new;
end;
$function$;

comment on function public.validate_capital_movement() is
    'Serializes Phase 1 ledger inserts and preserves both non-negative gross balance and the floor reserved by AUTHORIZED paper-capital authorizations.';


-- UPDATE already owns the simulation row lock before this BEFORE UPDATE row
-- trigger executes. Because authorization creation must acquire that same
-- simulation lock first, terminalization and reservation creation serialize
-- on one canonical financial mutex.
create function public.reject_simulation_terminalization_with_authorized_capital()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    has_active_authorization boolean;
begin
    if old.status = 'ACTIVE'
        and new.status in ('COMPLETED', 'CANCELLED')
    then
        select exists (
            select 1
            from public.operational_paper_capital_authorizations as capital_authorization
            where capital_authorization.simulation_id = old.id
              and capital_authorization.state = 'AUTHORIZED'
        )
        into has_active_authorization;

        if has_active_authorization then
            raise exception using
                errcode = '55000',
                message = 'simulation_terminalization_blocked_by_authorized_capital';
        end if;
    end if;

    return new;
end;
$function$;

comment on function public.reject_simulation_terminalization_with_authorized_capital() is
    'Prevents an ACTIVE Phase 1 simulation from becoming terminal while operational paper capital remains AUTHORIZED.';

create trigger simulation_runs_guard_authorized_capital_before_terminal
before update on public.simulation_runs
for each row
execute function public.reject_simulation_terminalization_with_authorized_capital();

-- B2A-4-END


alter table public.operational_paper_capital_authorizations
    enable row level security;

-- No RLS policies are created. Operational paper-capital authorization is
-- backend-only authority through the direct PostgreSQL owner connection.
revoke all privileges
    on table public.operational_paper_capital_authorizations
    from public, anon, authenticated, service_role;

-- Trigger functions are invariants, not callable APIs. Explicitly remove
-- Data API execution authority, including service_role.
revoke all privileges
    on function
        public.validate_operational_paper_capital_authorization_insert(),
        public.protect_operational_paper_capital_authorization(),
        public.reject_simulation_terminalization_with_authorized_capital(),
        public.validate_capital_movement()
    from public, anon, authenticated, service_role;

-- B2A-5-END
