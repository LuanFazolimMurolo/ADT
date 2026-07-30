-- ADT Phase 1D: make the FastAPI backend the only administrative writer.
--
-- The Phase 1A grants allowed authenticated administrators to mutate the base
-- tables through PostgREST.  That path bypasses the application services and
-- their authorization, transition and audit rules.  The backend intentionally
-- uses a direct PostgreSQL connection owned by a dedicated server-side role;
-- browser-facing Data API roles receive no base-table privileges.

do $migration_preflight$
begin
    if exists (
        select 1
        from public.system_settings
        where key !~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'
    ) then
        raise exception using
            errcode = '23514',
            message = 'Phase 1D cannot constrain system_settings.key while legacy invalid keys exist. Review and remediate them before retrying.';
    end if;
end;
$migration_preflight$;

alter table public.system_settings
add constraint system_settings_key_api_safe_check
check (key ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$');

-- Supabase grants broad privileges on future public-schema objects by default.
-- Change the defaults of the migration role so a later migration cannot
-- accidentally reopen the Data API boundary.
alter default privileges in schema public
revoke all privileges on tables
from public, anon, authenticated, service_role;

alter default privileges in schema public
revoke all privileges on sequences
from public, anon, authenticated, service_role;

-- PostgreSQL grants EXECUTE on new functions to PUBLIC as a global built-in
-- default. A schema-scoped REVOKE cannot subtract that global default, so both
-- levels must be closed.
alter default privileges
revoke execute on functions
from public, anon, authenticated, service_role;

alter default privileges in schema public
revoke all privileges on functions
from public, anon, authenticated, service_role;

create function public.require_active_simulation_for_movement()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    simulation_status text;
begin
    select simulation.status
    into simulation_status
    from public.simulation_runs as simulation
    where simulation.id = new.simulation_id
    for update;

    if found and simulation_status <> 'ACTIVE' then
        raise exception using
            errcode = '23514',
            message = 'Capital movements may be appended only to an ACTIVE simulation.';
    end if;

    return new;
end;
$function$;

comment on function public.require_active_simulation_for_movement() is
    'Defense in depth: rejects ledger entries after a simulation reaches a terminal state.';

create trigger capital_movements_require_active_before_insert
before insert on public.capital_movements
for each row
execute function public.require_active_simulation_for_movement();

create function public.enforce_simulation_terminal_state()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if old.status in ('COMPLETED', 'CANCELLED')
        and (
            new.status is distinct from old.status
            or new.ended_at is distinct from old.ended_at
        )
    then
        raise exception using
            errcode = '55000',
            message = 'A terminal simulation status and end time cannot be changed.';
    end if;

    return new;
end;
$function$;

comment on function public.enforce_simulation_terminal_state() is
    'Defense in depth: terminal simulation states and end times cannot be changed.';

create trigger simulation_runs_enforce_terminal_state
before update on public.simulation_runs
for each row
execute function public.enforce_simulation_terminal_state();

drop policy if exists app_admins_admin_read
    on public.app_admins;
drop policy if exists simulation_runs_admin_read
    on public.simulation_runs;
drop policy if exists simulation_runs_admin_insert
    on public.simulation_runs;
drop policy if exists simulation_runs_admin_update
    on public.simulation_runs;
drop policy if exists capital_movements_admin_read
    on public.capital_movements;
drop policy if exists capital_movements_admin_insert
    on public.capital_movements;
drop policy if exists system_settings_public_read
    on public.system_settings;
drop policy if exists system_settings_admin_all
    on public.system_settings;
drop policy if exists audit_logs_admin_read
    on public.audit_logs;
drop policy if exists audit_logs_admin_insert
    on public.audit_logs;

revoke all privileges
    on table
        public.app_admins,
        public.simulation_runs,
        public.capital_movements,
        public.system_settings,
        public.audit_logs,
        public.active_simulation_summary
    from public, anon, authenticated, service_role;

revoke all privileges
    on function
        public.validate_capital_movement(),
        public.reject_append_only_change(),
        public.protect_simulation_run_history(),
        public.set_updated_at(),
        public.is_adt_admin(),
        public.require_active_simulation_for_movement(),
        public.enforce_simulation_terminal_state()
    from public, anon, authenticated, service_role;

-- The 1A view still projected the internal simulation UUID even though the
-- public HTTP contract deliberately omits it. Recreate it instead of granting
-- column-level exceptions, so the Data API cannot introspect or filter by that
-- identifier.
drop view public.active_simulation_summary;

create view public.active_simulation_summary
with (security_barrier = true, security_invoker = false)
as
select
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

-- The owner-rights view is the complete anonymous/authenticated Data API
-- surface for Phase 1.  Its projection omits identifiers and administrative
-- data and its definition filters to the single ACTIVE paper simulation.
-- Revoke again after CREATE because Supabase default privileges may grant
-- DML on new objects in public to every Data API role.
revoke all privileges
    on table public.active_simulation_summary
    from public, anon, authenticated, service_role;

grant select
    on table public.active_simulation_summary
    to anon, authenticated;

comment on view public.active_simulation_summary is
    'Only Phase 1 Data API surface: UUID-free ACTIVE paper-simulation totals; base tables are backend-only.';
