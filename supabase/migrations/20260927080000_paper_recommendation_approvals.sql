create table public.paper_recommendation_approvals (
    approval_id uuid primary key,
    schema_version smallint not null,
    state text not null,
    record_version bigint not null,

    recommendation_identity text not null,
    recommendation_checksum text not null,

    candidate_identity text not null,
    candidate_checksum text not null,
    configuration_checksum text not null,
    parameters_checksum text not null,

    approval_checksum text not null,

    approved_by uuid not null,
    approved_at timestamptz not null,

    create_idempotency_key text not null,
    create_intent_fingerprint text not null,

    revoked_by uuid,
    revoked_at timestamptz,

    constraint paper_rec_approval_schema_check
        check (schema_version = 1),

    constraint paper_rec_approval_state_check
        check (state in ('APPROVED', 'REVOKED')),

    constraint paper_rec_approval_version_check
        check (record_version >= 1),

    constraint paper_rec_approval_rec_identity_check
        check (recommendation_identity ~ '^[0-9a-f]{64}$'),

    constraint paper_rec_approval_rec_checksum_check
        check (recommendation_checksum ~ '^[0-9a-f]{64}$'),

    constraint paper_rec_approval_candidate_identity_check
        check (candidate_identity ~ '^[0-9a-f]{64}$'),

    constraint paper_rec_approval_candidate_checksum_check
        check (candidate_checksum ~ '^[0-9a-f]{64}$'),

    constraint paper_rec_approval_config_checksum_check
        check (configuration_checksum ~ '^[0-9a-f]{64}$'),

    constraint paper_rec_approval_params_checksum_check
        check (parameters_checksum ~ '^[0-9a-f]{64}$'),

    constraint paper_rec_approval_checksum_check
        check (approval_checksum ~ '^[0-9a-f]{64}$'),

    constraint paper_rec_approval_fingerprint_check
        check (create_intent_fingerprint ~ '^[0-9a-f]{64}$'),

    constraint paper_rec_approval_idempotency_key_check
        check (
            char_length(create_idempotency_key) between 1 and 128
            and create_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
        ),

    constraint paper_rec_approval_lifecycle_check
        check (
            (
                state = 'APPROVED'
                and record_version = 1
                and revoked_by is null
                and revoked_at is null
            )
            or
            (
                state = 'REVOKED'
                and record_version >= 2
                and revoked_by is not null
                and revoked_at is not null
                and revoked_at >= approved_at
            )
        )
);

alter table public.paper_recommendation_approvals
    add constraint paper_rec_approval_actor_idempotency_key
    unique (approved_by, create_idempotency_key);

create index paper_rec_approval_recommendation_idx
    on public.paper_recommendation_approvals (
        recommendation_identity,
        recommendation_checksum
    );

create index paper_rec_approval_state_time_idx
    on public.paper_recommendation_approvals (
        state,
        approved_at desc,
        approval_id
    );

create function public.validate_paper_recommendation_approval_update()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if
        new.approval_id is distinct from old.approval_id
        or new.schema_version is distinct from old.schema_version
        or new.recommendation_identity is distinct from old.recommendation_identity
        or new.recommendation_checksum is distinct from old.recommendation_checksum
        or new.candidate_identity is distinct from old.candidate_identity
        or new.candidate_checksum is distinct from old.candidate_checksum
        or new.configuration_checksum is distinct from old.configuration_checksum
        or new.parameters_checksum is distinct from old.parameters_checksum
        or new.approval_checksum is distinct from old.approval_checksum
        or new.approved_by is distinct from old.approved_by
        or new.approved_at is distinct from old.approved_at
        or new.create_idempotency_key is distinct from old.create_idempotency_key
        or new.create_intent_fingerprint is distinct from old.create_intent_fingerprint
    then
        raise exception 'paper_recommendation_approval_binding_is_immutable'
            using errcode = '23514';
    end if;

    -- Lifecycle is intentionally one-way and terminal:
    -- APPROVED v1 -> REVOKED v2.
    if old.state <> 'APPROVED' or new.state <> 'REVOKED' then
        raise exception 'paper_recommendation_approval_state_transition_forbidden'
            using errcode = '23514';
    end if;

    if old.record_version <> 1
        or new.record_version <> old.record_version + 1
        or new.record_version <> 2
    then
        raise exception 'paper_recommendation_approval_record_version_conflict'
            using errcode = '40001';
    end if;

    if old.revoked_by is not null
        or old.revoked_at is not null
        or new.revoked_by is null
        or new.revoked_at is null
    then
        raise exception 'paper_recommendation_approval_revocation_audit_invalid'
            using errcode = '23514';
    end if;

    return new;
end;
$function$;

create trigger paper_rec_approval_validate_update
before update on public.paper_recommendation_approvals
for each row
execute function public.validate_paper_recommendation_approval_update();

alter table public.paper_recommendation_approvals enable row level security;

revoke all
    on table public.paper_recommendation_approvals
    from anon, authenticated;
