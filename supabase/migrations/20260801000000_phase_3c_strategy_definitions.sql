-- ADT Phase 3C: versioned reusable strategy definitions.
--
-- Strategy code remains an explicit server-side registry. This table stores
-- only validated plugin identities, exact versions and lossless parameters.

create table public.strategy_definitions (
    id uuid primary key default gen_random_uuid(),
    display_name text not null,
    display_name_key text generated always as (lower(btrim(display_name))) stored,
    plugin_name text not null,
    plugin_version text not null,
    plugin_schema_version integer not null,
    lifecycle_version integer not null,
    parameters jsonb not null,
    parameters_checksum text not null,
    state text not null default 'ACTIVE',
    revision bigint not null default 1,
    created_by uuid not null references auth.users (id) on delete restrict,
    updated_by uuid not null references auth.users (id) on delete restrict,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    archived_at timestamptz,
    constraint strategy_definitions_display_name_length_check
        check (display_name = btrim(display_name) and char_length(display_name) between 1 and 120),
    constraint strategy_definitions_plugin_name_check
        check (plugin_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    constraint strategy_definitions_plugin_version_check
        check (plugin_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    constraint strategy_definitions_plugin_schema_version_check
        check (plugin_schema_version >= 1),
    constraint strategy_definitions_lifecycle_version_check
        check (lifecycle_version >= 1),
    constraint strategy_definitions_parameters_object_check
        check (jsonb_typeof(parameters) = 'object'),
    constraint strategy_definitions_parameters_checksum_check
        check (parameters_checksum ~ '^[0-9a-f]{64}$'),
    constraint strategy_definitions_state_check
        check (state in ('ACTIVE', 'ARCHIVED')),
    constraint strategy_definitions_revision_check
        check (revision >= 1),
    constraint strategy_definitions_timestamps_check
        check (updated_at >= created_at),
    constraint strategy_definitions_archive_state_check
        check (
            (state = 'ACTIVE' and archived_at is null)
            or (
                state = 'ARCHIVED'
                and archived_at is not null
                and archived_at >= created_at
                and archived_at <= updated_at
            )
        ),
    constraint strategy_definitions_display_name_key_key unique (display_name_key)
);

create index strategy_definitions_state_created_idx
on public.strategy_definitions (state, created_at desc, id desc);

create function public.protect_strategy_definition_history()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'strategy_definitions records are historical and cannot be deleted.';
    end if;

    if old.state = 'ARCHIVED' then
        raise exception using
            errcode = '55000',
            message = 'An archived strategy definition cannot be changed.';
    end if;

    if new.id is distinct from old.id
        or new.created_by is distinct from old.created_by
        or new.created_at is distinct from old.created_at
    then
        raise exception using
            errcode = '55000',
            message = 'Strategy definition identity and creation metadata are immutable.';
    end if;

    if new.revision <> old.revision + 1 then
        raise exception using
            errcode = '23514',
            message = 'Strategy definition revision must increase by exactly one.';
    end if;

    if new.state not in ('ACTIVE', 'ARCHIVED') then
        raise exception using
            errcode = '23514',
            message = 'Strategy definition state transition is invalid.';
    end if;

    return new;
end;
$function$;

comment on function public.protect_strategy_definition_history() is
    'Enforces one-way archival, immutable creation metadata and exact revision increments.';

create trigger strategy_definitions_protect_history
before update or delete on public.strategy_definitions
for each row
execute function public.protect_strategy_definition_history();

create trigger strategy_definitions_set_updated_at
before update on public.strategy_definitions
for each row
execute function public.set_updated_at();

alter table public.strategy_definitions enable row level security;

revoke all privileges
on table public.strategy_definitions
from public, anon, authenticated, service_role;

revoke all privileges
on function public.protect_strategy_definition_history()
from public, anon, authenticated, service_role;

comment on table public.strategy_definitions is
    'Backend-only versioned strategy configurations; plugin code is never stored or dynamically imported.';
