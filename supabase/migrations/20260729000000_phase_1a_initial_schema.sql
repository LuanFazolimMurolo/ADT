-- ADT Phase 1A: initial Supabase schema.
-- This migration stores configuration and paper-trading bookkeeping only.
-- It contains no credentials, market logic, strategies, or real-capital support.

create table public.app_admins (
    user_id uuid not null,
    created_at timestamptz not null default now(),
    created_by uuid,
    constraint app_admins_pkey primary key (user_id),
    constraint app_admins_user_id_fkey
        foreign key (user_id)
        references auth.users (id)
        on delete cascade,
    constraint app_admins_created_by_fkey
        foreign key (created_by)
        references auth.users (id)
);

comment on table public.app_admins is
    'Closed allow-list of ADT administrators; there is no public enrollment.';

create table public.simulation_runs (
    id uuid not null default gen_random_uuid(),
    name text not null,
    status text not null,
    currency text not null default 'BRL',
    initial_capital numeric(20, 8) not null,
    started_at timestamptz not null default now(),
    ended_at timestamptz,
    created_by uuid not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint simulation_runs_pkey primary key (id),
    constraint simulation_runs_created_by_fkey
        foreign key (created_by)
        references auth.users (id),
    constraint simulation_runs_status_check
        check (status in ('ACTIVE', 'COMPLETED', 'CANCELLED')),
    constraint simulation_runs_name_not_blank_check
        check (btrim(name) <> ''),
    constraint simulation_runs_initial_capital_positive_check
        check (
            initial_capital not in (
                'NaN'::numeric,
                'Infinity'::numeric,
                '-Infinity'::numeric
            )
            and initial_capital > 0
        ),
    constraint simulation_runs_status_ended_at_check
        check (
            (status = 'ACTIVE' and ended_at is null)
            or (
                status in ('COMPLETED', 'CANCELLED')
                and ended_at is not null
            )
        ),
    constraint simulation_runs_ended_at_chronology_check
        check (ended_at is null or ended_at >= started_at),
    constraint simulation_runs_currency_not_blank_check
        check (btrim(currency) <> '')
);

comment on table public.simulation_runs is
    'Paper-trading simulation sessions. Historical runs are retained by default.';
comment on constraint simulation_runs_created_by_fkey on public.simulation_runs is
    'No cascading delete: simulation history must not disappear with an auth user.';

create table public.capital_movements (
    id uuid not null default gen_random_uuid(),
    simulation_id uuid not null,
    type text not null,
    amount numeric(20, 8) not null,
    reason text not null,
    reference_id uuid,
    created_by uuid,
    created_at timestamptz not null default now(),
    constraint capital_movements_pkey primary key (id),
    constraint capital_movements_simulation_id_fkey
        foreign key (simulation_id)
        references public.simulation_runs (id),
    constraint capital_movements_created_by_fkey
        foreign key (created_by)
        references auth.users (id),
    constraint capital_movements_type_check
        check (
            type in (
                'INITIAL_CAPITAL',
                'ADMIN_DEPOSIT',
                'ADMIN_WITHDRAWAL',
                'TRADE_PROFIT',
                'TRADE_LOSS',
                'FEE',
                'ADJUSTMENT'
            )
        ),
    constraint capital_movements_amount_nonzero_check
        check (
            amount not in (
                'NaN'::numeric,
                'Infinity'::numeric,
                '-Infinity'::numeric
            )
            and amount <> 0
        ),
    constraint capital_movements_reason_not_blank_check
        check (btrim(reason) <> ''),
    constraint capital_movements_amount_sign_check
        check (
            (
                type in ('INITIAL_CAPITAL', 'ADMIN_DEPOSIT', 'TRADE_PROFIT')
                and amount > 0
            )
            or (
                type in ('ADMIN_WITHDRAWAL', 'TRADE_LOSS', 'FEE')
                and amount < 0
            )
            or (
                type = 'ADJUSTMENT'
                and amount <> 0
            )
        )
);

comment on table public.capital_movements is
    'Append-only cash ledger. Corrections are new ADJUSTMENT rows, never rewrites.';
comment on constraint capital_movements_simulation_id_fkey
    on public.capital_movements is
    'No cascading delete: ledger entries prevent accidental simulation deletion.';

create table public.system_settings (
    key text not null,
    value jsonb not null,
    description text not null,
    is_public boolean not null default false,
    updated_by uuid,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint system_settings_pkey primary key (key),
    constraint system_settings_updated_by_fkey
        foreign key (updated_by)
        references auth.users (id),
    constraint system_settings_key_not_blank_check
        check (btrim(key) <> ''),
    constraint system_settings_description_not_blank_check
        check (btrim(description) <> '')
);

comment on table public.system_settings is
    'Non-secret ADT configuration. Credentials must never be stored in this table.';

create table public.audit_logs (
    id uuid not null default gen_random_uuid(),
    actor_user_id uuid,
    action text not null,
    entity_type text not null,
    entity_id uuid,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint audit_logs_pkey primary key (id),
    constraint audit_logs_actor_user_id_fkey
        foreign key (actor_user_id)
        references auth.users (id),
    constraint audit_logs_action_not_blank_check
        check (btrim(action) <> ''),
    constraint audit_logs_entity_type_not_blank_check
        check (btrim(entity_type) <> '')
);

comment on table public.audit_logs is
    'Append-only administrative audit trail; anonymous users cannot read it.';

-- The app_admins primary key already indexes its user_id foreign key.
create index app_admins_created_by_idx
    on public.app_admins (created_by);

create index simulation_runs_created_by_idx
    on public.simulation_runs (created_by);

-- A constant ACTIVE value can occur only once, while historical statuses repeat.
create unique index simulation_runs_single_active_uidx
    on public.simulation_runs (status)
    where status = 'ACTIVE';

-- This composite index covers the simulation foreign key and ordered ledger reads.
create index capital_movements_simulation_created_at_idx
    on public.capital_movements (simulation_id, created_at);

create index capital_movements_created_by_idx
    on public.capital_movements (created_by);

-- A simulation has exactly one opening entry at most. The validation trigger
-- additionally requires that entry before every other movement.
create unique index capital_movements_single_initial_capital_uidx
    on public.capital_movements (simulation_id)
    where type = 'INITIAL_CAPITAL';

create index system_settings_updated_by_idx
    on public.system_settings (updated_by);

create index audit_logs_actor_user_id_idx
    on public.audit_logs (actor_user_id);

-- Locking the parent simulation serializes every balance-changing insertion for
-- that simulation. The balance is then calculated exclusively with numeric
-- arithmetic while the lock is held.
create function public.validate_capital_movement()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    simulation_initial_capital numeric;
    current_balance numeric;
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

    if current_balance + new.amount < 0::numeric then
        raise exception using
            errcode = '23514',
            message = 'The capital movement would make the simulation balance negative.';
    end if;

    return new;
end;
$function$;

comment on function public.validate_capital_movement() is
    'Serializes movement inserts and enforces opening-capital and non-negative-balance rules.';

create trigger capital_movements_validate_before_insert
before insert on public.capital_movements
for each row
execute function public.validate_capital_movement();

-- RLS and table grants are not the append-only boundary: these triggers also
-- reject changes made through direct database connections that bypass RLS.
create function public.reject_append_only_change()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_table_name = 'capital_movements' then
        raise exception using
            errcode = '55000',
            message = 'capital_movements is append-only; record corrections with a new ADJUSTMENT movement.';
    end if;

    raise exception using
        errcode = '55000',
        message = 'audit_logs is append-only and cannot be updated or deleted.';
end;
$function$;

comment on function public.reject_append_only_change() is
    'Rejects UPDATE and DELETE operations against append-only ADT records.';

create trigger capital_movements_reject_update_delete
before update or delete on public.capital_movements
for each row
execute function public.reject_append_only_change();

create trigger audit_logs_reject_update_delete
before update or delete on public.audit_logs
for each row
execute function public.reject_append_only_change();

create function public.protect_simulation_run_history()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'simulation_runs records are historical and cannot be deleted.';
    end if;

    if new.id is distinct from old.id
        or new.initial_capital is distinct from old.initial_capital
        or new.created_by is distinct from old.created_by
        or new.created_at is distinct from old.created_at
        or new.started_at is distinct from old.started_at
        or new.currency is distinct from old.currency
    then
        raise exception using
            errcode = '55000',
            message = 'Immutable simulation history fields cannot be changed.';
    end if;

    return new;
end;
$function$;

comment on function public.protect_simulation_run_history() is
    'Prevents deletion and preserves immutable identity, ownership, date, currency, and capital fields.';

create trigger simulation_runs_protect_history
before update or delete on public.simulation_runs
for each row
execute function public.protect_simulation_run_history();

-- Keep updated_at authoritative in the database for every mutable table that has it.
create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    new.updated_at := now();
    return new;
end;
$function$;

comment on function public.set_updated_at() is
    'Reusable trigger function that records the database update time.';

create trigger simulation_runs_set_updated_at
before update on public.simulation_runs
for each row
execute function public.set_updated_at();

create trigger system_settings_set_updated_at
before update on public.system_settings
for each row
execute function public.set_updated_at();

-- SECURITY DEFINER is required here to avoid recursive RLS while checking the
-- protected allow-list. An empty search_path plus fully-qualified object names
-- prevents callers from substituting objects used by this function.
create function public.is_adt_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $function$
    select exists (
        select 1
        from public.app_admins as admin
        where admin.user_id = auth.uid()
    );
$function$;

comment on function public.is_adt_admin() is
    'Returns whether the authenticated user belongs to the closed ADT admin allow-list.';

-- Trigger functions are not callable APIs. The authorization helper is exposed
-- only to authenticated requests because no anonymous policy needs it.
revoke all privileges on function public.validate_capital_movement()
    from public, anon, authenticated;
revoke all privileges on function public.reject_append_only_change()
    from public, anon, authenticated;
revoke all privileges on function public.protect_simulation_run_history()
    from public, anon, authenticated;
revoke all privileges on function public.set_updated_at() from public, anon, authenticated;
revoke all privileges on function public.is_adt_admin() from public, anon, authenticated;
grant execute on function public.is_adt_admin() to authenticated;

insert into public.system_settings (key, value, description, is_public)
values
    (
        'system_name',
        '"ADT"'::jsonb,
        'Public display name of the system.',
        true
    ),
    (
        'paper_trading_enabled',
        'true'::jsonb,
        'Whether paper-trading functionality is enabled.',
        true
    ),
    (
        'public_dashboard_enabled',
        'true'::jsonb,
        'Whether the public dashboard is enabled.',
        true
    ),
    (
        'default_currency',
        '"BRL"'::jsonb,
        'Default currency code used by ADT simulations.',
        true
    )
on conflict (key) do nothing;

alter table public.app_admins enable row level security;
alter table public.simulation_runs enable row level security;
alter table public.capital_movements enable row level security;
alter table public.system_settings enable row level security;
alter table public.audit_logs enable row level security;

-- The allow-list is read-only through the Data API. Its sole initial write path
-- is the controlled bootstrap over a direct PostgreSQL connection.
create policy app_admins_admin_read
on public.app_admins
for select
to authenticated
using ((select public.is_adt_admin()));

create policy simulation_runs_admin_read
on public.simulation_runs
for select
to authenticated
using ((select public.is_adt_admin()));

create policy simulation_runs_admin_insert
on public.simulation_runs
for insert
to authenticated
with check ((select public.is_adt_admin()));

create policy simulation_runs_admin_update
on public.simulation_runs
for update
to authenticated
using ((select public.is_adt_admin()))
with check ((select public.is_adt_admin()));

-- The cash ledger is append-only through application roles: administrators may
-- read and append entries, but no UPDATE or DELETE policy exists.
create policy capital_movements_admin_read
on public.capital_movements
for select
to authenticated
using ((select public.is_adt_admin()));

create policy capital_movements_admin_insert
on public.capital_movements
for insert
to authenticated
with check ((select public.is_adt_admin()));

create policy system_settings_public_read
on public.system_settings
for select
to anon, authenticated
using (is_public = true);

create policy system_settings_admin_all
on public.system_settings
for all
to authenticated
using ((select public.is_adt_admin()))
with check ((select public.is_adt_admin()));

-- Audit records follow the same append-only model: administrative readers and
-- writers receive no policy that would permit rewriting history.
create policy audit_logs_admin_read
on public.audit_logs
for select
to authenticated
using ((select public.is_adt_admin()));

create policy audit_logs_admin_insert
on public.audit_logs
for insert
to authenticated
with check ((select public.is_adt_admin()));

-- Start from explicit privileges instead of relying on Supabase default grants.
-- RLS remains the second authorization layer for every authenticated operation.
revoke all privileges on table public.app_admins
    from public, anon, authenticated, service_role;
revoke all privileges on table public.simulation_runs
    from public, anon, authenticated;
revoke delete on table public.simulation_runs
    from service_role;
revoke all privileges on table public.capital_movements
    from public, anon, authenticated;
revoke all privileges on table public.system_settings
    from public, anon, authenticated;
revoke all privileges on table public.audit_logs
    from public, anon, authenticated;

grant select
    on table public.app_admins
    to authenticated;
grant select, insert, update
    on table public.simulation_runs
    to authenticated;
grant select, insert
    on table public.capital_movements
    to authenticated;
grant select, insert, update, delete
    on table public.system_settings
    to authenticated;
grant select, insert
    on table public.audit_logs
    to authenticated;
grant select
    on table public.system_settings
    to anon;

-- This owner-rights view is an intentional, narrow public security boundary.
-- security_barrier prevents caller predicates from being pushed below the fixed
-- projection/filter. Base tables remain unavailable to anonymous callers, and
-- the view exposes neither administrator identifiers nor movement-level data.
create view public.active_simulation_summary
with (security_barrier = true, security_invoker = false)
as
select
    simulation.id as simulation_id,
    simulation.name as simulation_name,
    simulation.currency,
    simulation.initial_capital,
    movement_totals.current_balance,
    movement_totals.total_profit_loss,
    simulation.started_at,
    simulation.status
from public.simulation_runs as simulation
left join lateral (
    select
        coalesce(sum(movement.amount), 0::numeric) as current_balance,
        coalesce(
            sum(movement.amount) filter (
                where movement.type in ('TRADE_PROFIT', 'TRADE_LOSS', 'FEE')
            ),
            0::numeric
        ) as total_profit_loss
    from public.capital_movements as movement
    where movement.simulation_id = simulation.id
) as movement_totals on true
where simulation.status = 'ACTIVE';

comment on view public.active_simulation_summary is
    'Public active paper-simulation totals with no administrator or audit data.';

revoke all privileges on table public.active_simulation_summary
    from public, anon, authenticated;
grant select on table public.active_simulation_summary
    to anon, authenticated;
