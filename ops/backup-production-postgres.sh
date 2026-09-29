#!/usr/bin/env bash
set -euo pipefail

MODE="dry-run"

if [ "${1:-}" = "--execute" ]; then
    MODE="execute"
elif [ -n "${1:-}" ]; then
    echo "usage: $0 [--execute]" >&2
    exit 2
fi

DATABASE_URL="${ADT_POSTGRES_BACKUP_URL:-}"
BACKUP_ROOT="${ADT_BACKUP_ROOT:-}"
RECOVERY_SET_ID="${ADT_RECOVERY_SET_ID:-}"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1         || fail "required command not found: $1"
}

[ -n "$BACKUP_ROOT" ]     || fail "ADT_BACKUP_ROOT is required"

case "$BACKUP_ROOT" in
    /*) ;;
    *) fail "ADT_BACKUP_ROOT must be absolute" ;;
esac

[ "$BACKUP_ROOT" != "/" ]     || fail "refusing filesystem root as ADT_BACKUP_ROOT"

echo "ADT PostgreSQL logical backup"
echo "mode: $MODE"
echo "backup-root: $BACKUP_ROOT"
echo "scope: public schema and public data"

if [ "$MODE" = "dry-run" ]; then
    echo "DRY RUN: no database connection will be opened"
    echo "DRY RUN: ADT_POSTGRES_BACKUP_URL is required only with --execute"
    echo "DRY RUN: execution requires ADT_BACKUP_QUIESCED=YES"

    exit 0
fi

[ "${ADT_BACKUP_QUIESCED:-}" = "YES" ]     || fail "execution requires ADT_BACKUP_QUIESCED=YES"

[ -n "$RECOVERY_SET_ID" ]     || fail "execution requires ADT_RECOVERY_SET_ID"

[ "${#RECOVERY_SET_ID}" -le 128 ]     || fail "ADT_RECOVERY_SET_ID must be at most 128 characters"

case "$RECOVERY_SET_ID" in
    *[!A-Za-z0-9._-]*)
        fail "ADT_RECOVERY_SET_ID contains invalid characters"
        ;;
esac


[ -n "$DATABASE_URL" ]     || fail "ADT_POSTGRES_BACKUP_URL is required for execution"

require_command pg_dump
require_command sha256sum
require_command date
require_command mv

umask 077

mkdir -p "$BACKUP_ROOT"

[ ! -L "$BACKUP_ROOT" ]     || fail "ADT_BACKUP_ROOT must not be a symbolic link"

BACKUP_ID="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_DIR="${BACKUP_ROOT%/}/postgres-${BACKUP_ID}"
STAGING_DIR="${BACKUP_ROOT%/}/.postgres-${BACKUP_ID}.tmp"

[ ! -e "$FINAL_DIR" ]     || fail "backup destination already exists"

[ ! -e "$STAGING_DIR" ]     || fail "backup staging directory already exists"

cleanup() {
    if [ -d "$STAGING_DIR" ]; then
        rm -rf -- "$STAGING_DIR"
    fi
}

trap cleanup EXIT INT TERM

mkdir -m 0700 "$STAGING_DIR"

PRE_DATA_FILE="$STAGING_DIR/pre-data.sql"
DATA_FILE="$STAGING_DIR/data.dump"
POST_DATA_FILE="$STAGING_DIR/post-data.sql"
CHECKSUMS="$STAGING_DIR/SHA256SUMS"
METADATA="$STAGING_DIR/metadata.txt"

pg_dump "$DATABASE_URL" --schema=public --section=pre-data --no-owner --file="$PRE_DATA_FILE"

pg_dump "$DATABASE_URL" --schema=public --section=data --format=custom --no-owner --file="$DATA_FILE"

pg_dump "$DATABASE_URL" --schema=public --section=post-data --no-owner --file="$POST_DATA_FILE"

{
    echo "schema_version=3"
    echo "backup_id=$BACKUP_ID"
    echo "recovery_set_id=$RECOVERY_SET_ID"
    echo "created_at_utc=$BACKUP_ID"
    echo "schema_scope=public"
    echo "data_scope=public"
    echo "pre_data_file=pre-data.sql"
    echo "data_file=data.dump"
    echo "post_data_file=post-data.sql"
    echo "quiescence_asserted=true"
    echo "managed_supabase_backup_required=true"
} > "$METADATA"

(
    cd "$STAGING_DIR"
    sha256sum pre-data.sql data.dump post-data.sql metadata.txt > SHA256SUMS
    sha256sum -c SHA256SUMS
)

mv -- "$STAGING_DIR" "$FINAL_DIR"
trap - EXIT INT TERM

echo "PASS: PostgreSQL logical backup created"
echo "backup-set: $FINAL_DIR"
echo "NOTE: this is an ADT application backup, not a full Supabase platform backup"
