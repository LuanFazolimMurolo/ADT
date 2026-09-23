-- Permit a fenced PAUSED -> STOPPING claim for PAPER finalization.
-- Existing identity, version, lease and terminal guards remain unchanged.

create or replace function public.op_ps_run_transition_allowed(
    old_state text,
    new_state text
)
returns boolean
language sql
immutable
strict
set search_path = ''
as $function$
    select case old_state
        when 'PENDING' then
            new_state in ('STARTING', 'PAUSED', 'STOPPED', 'FAILED')
        when 'STARTING' then
            new_state in (
                'RUNNING',
                'PAUSED',
                'RECOVERING',
                'STOPPING',
                'FAILED'
            )
        when 'RUNNING' then
            new_state in (
                'PAUSED',
                'RECOVERING',
                'STOPPING',
                'FAILED'
            )
        when 'PAUSED' then
            new_state in ('STARTING', 'STOPPING', 'STOPPED', 'FAILED')
        when 'RECOVERING' then
            new_state in (
                'STARTING',
                'PAUSED',
                'STOPPING',
                'STOPPED',
                'FAILED'
            )
        when 'STOPPING' then
            new_state in ('RECOVERING', 'STOPPED', 'FAILED')
        when 'STOPPED' then false
        when 'FAILED' then false
        else false
    end;
$function$;

create or replace function public.protect_op_ps_run_epoch()
returns trigger
language plpgsql
set search_path = ''
as $function$
declare
    administrative_command_type text;
    expected_command_type text;
begin
    if tg_op = 'DELETE' then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_delete_forbidden';
    end if;

    -- Historical identity and upstream provenance are immutable.
    if new.epoch_id is distinct from old.epoch_id
        or new.schema_version is distinct from old.schema_version
        or new.run_contract_version is distinct from old.run_contract_version

        or new.activation_id is distinct from old.activation_id
        or new.activation_checksum is distinct from old.activation_checksum

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
        or new.epoch_checksum is distinct from old.epoch_checksum

        or new.start_requested_by is distinct from old.start_requested_by
        or new.start_requested_at is distinct from old.start_requested_at
        or new.start_idempotency_key is distinct from old.start_idempotency_key
        or new.start_intent_fingerprint is distinct from old.start_intent_fingerprint
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_immutable_fields_changed';
    end if;

    if new.record_version is distinct from old.record_version + 1 then
        raise exception using
            errcode = '40001',
            message = 'operational_paper_session_run_epoch_record_version_conflict';
    end if;

    if old.observed_state in ('STOPPED', 'FAILED') then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_terminal';
    end if;

    if new.fencing_token < old.fencing_token
        or new.fencing_token > old.fencing_token + 1
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_fencing_token_invalid';
    end if;

    -- Desired-state changes are administrative command mutations only.
    if new.desired_state is distinct from old.desired_state then
        if new.observed_state is distinct from old.observed_state
            or new.fencing_token is distinct from old.fencing_token

            or new.worker_id is distinct from old.worker_id
            or new.worker_claimed_at is distinct from old.worker_claimed_at
            or new.worker_heartbeat_at is distinct from old.worker_heartbeat_at
            or new.worker_lease_expires_at is distinct from old.worker_lease_expires_at

            or new.failure_code is distinct from old.failure_code
            or new.failure_at is distinct from old.failure_at
            or new.terminal_at is distinct from old.terminal_at
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_command_mixed_mutation';
        end if;

        expected_command_type :=
            case
                when old.desired_state = 'RUNNING'
                    and new.desired_state = 'PAUSED'
                    then 'PAUSE'

                when old.desired_state = 'RUNNING'
                    and new.desired_state = 'STOPPED'
                    then 'STOP'

                when old.desired_state = 'PAUSED'
                    and new.desired_state = 'RUNNING'
                    then 'RESUME'

                when old.desired_state = 'PAUSED'
                    and new.desired_state = 'STOPPED'
                    then 'STOP'

                else null
            end;

        if expected_command_type is null then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_desired_transition_forbidden';
        end if;

        select command.command_type
        into administrative_command_type
        from public.operational_paper_session_run_commands as command
        where command.epoch_id = old.epoch_id
          and command.epoch_checksum = old.epoch_checksum
          and command.expected_record_version = old.record_version
          and command.resulting_record_version = new.record_version
          and command.desired_state = new.desired_state;

        if not found then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_command_required';
        end if;

        if administrative_command_type is distinct from expected_command_type then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_command_type_mismatch';
        end if;

        return new;
    end if;

    -- Worker/state mutations must never consume an administrative command
    -- record version.
    if exists (
        select 1
        from public.operational_paper_session_run_commands as command
        where command.epoch_id = old.epoch_id
          and command.resulting_record_version = new.record_version
    ) then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_unexpected_command_version';
    end if;

    -- No observed transition may bypass the frozen pure-domain graph.
    if new.observed_state is distinct from old.observed_state
        and not public.op_ps_run_transition_allowed(
            old.observed_state,
            new.observed_state
        )
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_observed_transition_forbidden';
    end if;

    -- Prevent a pure record-version bump with no semantic mutation.
    if new.observed_state is not distinct from old.observed_state
        and new.fencing_token is not distinct from old.fencing_token

        and new.worker_id is not distinct from old.worker_id
        and new.worker_claimed_at is not distinct from old.worker_claimed_at
        and new.worker_heartbeat_at is not distinct from old.worker_heartbeat_at
        and new.worker_lease_expires_at is not distinct from old.worker_lease_expires_at

        and new.failure_code is not distinct from old.failure_code
        and new.failure_at is not distinct from old.failure_at
        and new.terminal_at is not distinct from old.terminal_at
    then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_noop_mutation';
    end if;

    -- A new or reclaimed worker capability always advances the fencing token
    -- by exactly one.
    if new.fencing_token = old.fencing_token + 1 then
        if new.worker_id is null
            or new.worker_claimed_at is null
            or new.worker_heartbeat_at is null
            or new.worker_lease_expires_at is null
            or new.worker_claimed_at is distinct from new.worker_heartbeat_at
            or new.worker_claimed_at < new.start_requested_at
            or new.worker_lease_expires_at <= new.worker_heartbeat_at
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_new_claim_invalid';
        end if;

        if old.worker_id is null then
            -- Initial/resume execution claim or a dedicated paused STOP claim.
            if not (
                (
                    old.observed_state in ('PENDING', 'PAUSED')
                    and old.desired_state = 'RUNNING'
                    and new.desired_state = 'RUNNING'
                    and new.observed_state = 'STARTING'
                )
                or (
                    old.observed_state = 'PAUSED'
                    and old.desired_state = 'STOPPED'
                    and new.desired_state = 'STOPPED'
                    and new.observed_state = 'STOPPING'
                )
            )
            then
                raise exception using
                    errcode = '55000',
                    message = 'operational_paper_session_run_epoch_claim_transition_invalid';
            end if;
        else
            -- Crash recovery keeps the same epoch and advances the fence.
            if old.worker_lease_expires_at is null
                or new.worker_claimed_at < old.worker_lease_expires_at
                or new.observed_state <> 'RECOVERING'
                or new.desired_state is distinct from old.desired_state
            then
                raise exception using
                    errcode = '55000',
                    message = 'operational_paper_session_run_epoch_recovery_invalid';
            end if;
        end if;

        return new;
    end if;

    -- From here the fence is unchanged.
    if new.fencing_token is distinct from old.fencing_token then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_fence_mutation_invalid';
    end if;

    -- A worker cannot appear under an existing fence.
    if old.worker_id is null and new.worker_id is not null then
        raise exception using
            errcode = '55000',
            message = 'operational_paper_session_run_epoch_worker_requires_new_fence';
    end if;

    if old.worker_id is not null and new.worker_id is not null then
        if new.worker_id is distinct from old.worker_id
            or new.worker_claimed_at is distinct from old.worker_claimed_at
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_worker_identity_changed';
        end if;

        -- Heartbeat and lease extension are one mutation.
        if (
            new.worker_heartbeat_at is distinct from old.worker_heartbeat_at
            or new.worker_lease_expires_at is distinct from old.worker_lease_expires_at
        ) then
            if new.worker_heartbeat_at is null
                or old.worker_heartbeat_at is null
                or new.worker_lease_expires_at is null
                or old.worker_lease_expires_at is null
                or new.worker_heartbeat_at <= old.worker_heartbeat_at
                or new.worker_heartbeat_at >= old.worker_lease_expires_at
                or new.worker_lease_expires_at <= old.worker_lease_expires_at
                or new.worker_heartbeat_at >= new.worker_lease_expires_at
            then
                raise exception using
                    errcode = '55000',
                    message = 'operational_paper_session_run_epoch_lease_renewal_invalid';
            end if;
        end if;
    end if;

    -- Releasing a capability is only a boundary settlement/failure.
    if old.worker_id is not null and new.worker_id is null then
        if new.observed_state not in ('PAUSED', 'STOPPED', 'FAILED') then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_worker_release_invalid';
        end if;
    end if;

    -- An unclaimed epoch can only settle/fail without introducing a worker.
    if old.worker_id is null and new.worker_id is null then
        if old.observed_state = 'PENDING'
            and new.observed_state not in (
                'PENDING',
                'PAUSED',
                'STOPPED',
                'FAILED'
            )
        then
            raise exception using
                errcode = '55000',
                message = 'operational_paper_session_run_epoch_unclaimed_transition_invalid';
        end if;
    end if;

    -- Failure/terminal timestamps cannot precede the latest claimed activity.
    if old.worker_heartbeat_at is not null then
        if new.failure_at is not null
            and new.failure_at < old.worker_heartbeat_at
        then
            raise exception using
                errcode = '23514',
                message = 'operational_paper_session_run_epoch_failure_at_invalid';
        end if;

        if new.terminal_at is not null
            and new.terminal_at < old.worker_heartbeat_at
        then
            raise exception using
                errcode = '23514',
                message = 'operational_paper_session_run_epoch_terminal_at_invalid';
        end if;
    end if;

    return new;
end;
$function$;
