-- ADT Phase 7-14 Gate 2E:
-- settled-session START prevention and official-simulation terminalization guards.
--
-- This migration does not create a second financial authority.
-- Phase 1 simulation_runs/capital_movements remain authoritative for simulated
-- capital, and Phase 7-14 settlements remain immutable terminal evidence.
--
-- The existing Phase 7-08 simulation terminalization guard continues to own the
-- AUTHORIZED-reservation rule. Gate 2E adds the remaining official-era rules:
--
-- 1. a financially settled session cannot create a future run epoch;
-- 2. an official simulation cannot terminalize with a non-terminal run epoch;
-- 3. an official simulation cannot terminalize while any historical session_id
--    lacks settlement, including FAILED or otherwise ambiguous history.
--
-- START and settlement serialize on the canonical simulation row. This closes
-- the race in which settlement and a future START inspect financial finality
-- concurrently. Historical START replay does not insert a new epoch and remains
-- outside this trigger.

create function public.guard_op_ps_run_settled_session_start()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    -- Match the canonical financial lock order used by authorization,
    -- capital movement and Phase 7-14 settlement transactions.
    perform 1
    from public.simulation_runs as simulation
    where simulation.id = new.simulation_id
    for update;

    -- Preserve the existing run-insert validator as authority for a missing
    -- simulation. This guard is only responsible for settlement finality.
    if not found then
        return new;
    end if;

    if exists (
        select 1
        from public.operational_paper_session_settlements as settlement
        where settlement.session_id = new.session_id
    ) then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_session_already_settled';
    end if;

    return new;
end;
$function$;

comment on function public.guard_op_ps_run_settled_session_start() is
    'Serializes run START with settlement and rejects creation of future epochs for a financially settled session_id.';

-- PostgreSQL fires triggers of the same kind in name order. The 00 prefix makes
-- financial-finality rejection occur before the pre-existing run START
-- authority validator, so a settled session cannot be reopened through a later
-- authorization/materialization chain.
create trigger op_ps_run_epoch_00_settlement_finality
before insert on public.operational_paper_session_run_epochs
for each row
execute function public.guard_op_ps_run_settled_session_start();


create function public.guard_official_simulation_terminalization()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    is_official boolean;
    has_nonterminal_run boolean;
    has_unsettled_session boolean;
begin
    if old.status = 'ACTIVE'
        and new.status in ('COMPLETED', 'CANCELLED')
    then
        select exists (
            select 1
            from public.operational_paper_capital_eras as era
            where era.simulation_id = old.id
        )
        into is_official;

        if not is_official then
            return new;
        end if;

        -- A simulation UPDATE already owns the simulation row lock. START and
        -- settlement must acquire that same mutex, so these reads are stable
        -- against financial-finality races through commit.
        select exists (
            select 1
            from public.operational_paper_session_run_epochs as epoch
            where epoch.simulation_id = old.id
              and epoch.observed_state not in ('STOPPED', 'FAILED')
        )
        into has_nonterminal_run;

        if has_nonterminal_run then
            raise exception using
                errcode = '55000',
                message = 'official_paper_simulation_terminalization_blocked_by_nonterminal_run';
        end if;

        -- STOPPED-but-unsettled and FAILED histories both remain unresolved.
        -- FAILED is intentionally not settlement eligible in Phase 7-14 and
        -- therefore requires the separately reviewed future reconciliation
        -- contract before clean official-era closure.
        select exists (
            select 1
            from public.operational_paper_session_run_epochs as epoch
            where epoch.simulation_id = old.id
              and not exists (
                  select 1
                  from public.operational_paper_session_settlements as settlement
                  where settlement.session_id = epoch.session_id
                    and settlement.simulation_id = old.id
              )
        )
        into has_unsettled_session;

        if has_unsettled_session then
            raise exception using
                errcode = '55000',
                message = 'official_paper_simulation_terminalization_blocked_by_unsettled_session';
        end if;
    end if;

    return new;
end;
$function$;

comment on function public.guard_official_simulation_terminalization() is
    'Blocks clean official-simulation terminalization while operational run history is non-terminal or lacks immutable session settlement.';

create trigger simulation_runs_guard_official_paper_finality_before_terminal
before update on public.simulation_runs
for each row
execute function public.guard_official_simulation_terminalization();


-- Trigger functions are backend invariants, not callable application APIs.
revoke all privileges
    on function
        public.guard_op_ps_run_settled_session_start(),
        public.guard_official_simulation_terminalization()
    from public, anon, authenticated, service_role;
