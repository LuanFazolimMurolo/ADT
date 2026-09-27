#!/usr/bin/env bash
set -euo pipefail

MODE="dry-run"

if [ "${1:-}" = "--execute" ]; then
    MODE="execute"
elif [ -n "${1:-}" ]; then
    echo "usage: $0 [--execute]" >&2
    exit 2
fi

DATA_DIR="${ADT_DATA_DIR:-/srv/adt/data}"
BACKUP_ROOT="${ADT_BACKUP_ROOT:-}"
RECOVERY_SET_ID="${ADT_RECOVERY_SET_ID:-}"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1         || fail "required command not found: $1"
}

case "$DATA_DIR" in
    /*) ;;
    *) fail "ADT_DATA_DIR must be absolute" ;;
esac

[ -n "$BACKUP_ROOT" ]     || fail "ADT_BACKUP_ROOT is required"

case "$BACKUP_ROOT" in
    /*) ;;
    *) fail "ADT_BACKUP_ROOT must be absolute" ;;
esac

[ "$DATA_DIR" != "/" ]     || fail "refusing filesystem root as ADT_DATA_DIR"

[ "$BACKUP_ROOT" != "/" ]     || fail "refusing filesystem root as ADT_BACKUP_ROOT"

case "${BACKUP_ROOT%/}/" in
    "${DATA_DIR%/}/"*)
        fail "ADT_BACKUP_ROOT must not be inside ADT_DATA_DIR"
        ;;
esac

case "${DATA_DIR%/}/" in
    "${BACKUP_ROOT%/}/"*)
        fail "ADT_DATA_DIR must not be inside ADT_BACKUP_ROOT"
        ;;
esac

echo "ADT production filesystem backup"
echo "mode: $MODE"
echo "data-dir: $DATA_DIR"
echo "backup-root: $BACKUP_ROOT"

if [ "$MODE" = "dry-run" ]; then
    echo "DRY RUN: no files will be created"
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


[ -d "$DATA_DIR" ]     || fail "ADT_DATA_DIR does not exist"

[ ! -L "$DATA_DIR" ]     || fail "ADT_DATA_DIR must not be a symbolic link"

require_command tar
require_command zstd
require_command sha256sum
require_command date
require_command stat
require_command mv

umask 077

mkdir -p "$BACKUP_ROOT"

[ ! -L "$BACKUP_ROOT" ]     || fail "ADT_BACKUP_ROOT must not be a symbolic link"

BACKUP_ID="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_DIR="${BACKUP_ROOT%/}/filesystem-${BACKUP_ID}"
STAGING_DIR="${BACKUP_ROOT%/}/.filesystem-${BACKUP_ID}.tmp"

[ ! -e "$FINAL_DIR" ]     || fail "backup destination already exists"

[ ! -e "$STAGING_DIR" ]     || fail "backup staging directory already exists"

cleanup() {
    if [ -d "$STAGING_DIR" ]; then
        rm -rf -- "$STAGING_DIR"
    fi
}

trap cleanup EXIT INT TERM

mkdir -m 0700 "$STAGING_DIR"

ARCHIVE="$STAGING_DIR/adt-data.tar.zst"
CHECKSUMS="$STAGING_DIR/SHA256SUMS"
METADATA="$STAGING_DIR/metadata.txt"

DATA_PARENT="$(dirname "$DATA_DIR")"
DATA_NAME="$(basename "$DATA_DIR")"

tar     --numeric-owner     --acls     --xattrs     -C "$DATA_PARENT"     -cf -     "$DATA_NAME"     | zstd -T0 -19 -o "$ARCHIVE"

{
    echo "schema_version=2"
    echo "backup_id=$BACKUP_ID"
    echo "recovery_set_id=$RECOVERY_SET_ID"
    echo "created_at_utc=$BACKUP_ID"
    echo "source_data_dir=$DATA_DIR"
    echo "archive=adt-data.tar.zst"
    echo "quiescence_asserted=true"
    echo "uid_expected=10001"
    echo "gid_expected=10001"
} > "$METADATA"

(
    cd "$STAGING_DIR"
    sha256sum adt-data.tar.zst metadata.txt > SHA256SUMS
)

(
    cd "$STAGING_DIR"
    sha256sum -c SHA256SUMS
)

mv -- "$STAGING_DIR" "$FINAL_DIR"
trap - EXIT INT TERM

echo "PASS: filesystem backup created"
echo "backup-set: $FINAL_DIR"
