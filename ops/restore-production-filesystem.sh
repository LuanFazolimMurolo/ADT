#!/usr/bin/env bash
set -euo pipefail

MODE="dry-run"

if [ "${1:-}" = "--execute" ]; then
    MODE="execute"
elif [ -n "${1:-}" ]; then
    echo "usage: $0 [--execute]" >&2
    exit 2
fi

BACKUP_SET="${ADT_RESTORE_BACKUP_SET:-}"
RESTORE_ROOT="${ADT_RESTORE_ROOT:-}"
EXPECTED_RECOVERY_SET_ID="${ADT_EXPECTED_RECOVERY_SET_ID:-}"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1         || fail "required command not found: $1"
}

[ -n "$BACKUP_SET" ]     || fail "ADT_RESTORE_BACKUP_SET is required"

[ -n "$RESTORE_ROOT" ]     || fail "ADT_RESTORE_ROOT is required"

case "$BACKUP_SET" in
    /*) ;;
    *) fail "ADT_RESTORE_BACKUP_SET must be absolute" ;;
esac

case "$RESTORE_ROOT" in
    /*) ;;
    *) fail "ADT_RESTORE_ROOT must be absolute" ;;
esac

[ "$RESTORE_ROOT" != "/" ]     || fail "refusing filesystem root as ADT_RESTORE_ROOT"

[ "$RESTORE_ROOT" != "/srv/adt/data" ]     || fail "direct restore into production ADT_DATA_DIR is prohibited"

[ -d "$BACKUP_SET" ]     || fail "backup set does not exist"

[ ! -L "$BACKUP_SET" ]     || fail "backup set must not be a symbolic link"

ARCHIVE="$BACKUP_SET/adt-data.tar.zst"
CHECKSUMS="$BACKUP_SET/SHA256SUMS"
METADATA="$BACKUP_SET/metadata.txt"

[ -f "$ARCHIVE" ]     || fail "backup archive missing"

[ -f "$CHECKSUMS" ]     || fail "backup checksum file missing"

[ -f "$METADATA" ]     || fail "backup metadata file missing"

require_command sha256sum
require_command tar
require_command zstd
require_command awk
require_command grep
require_command find
require_command stat

echo "ADT production filesystem restore"
echo "mode: $MODE"
echo "backup-set: $BACKUP_SET"
echo "restore-root: $RESTORE_ROOT"

(
    cd "$BACKUP_SET"
    sha256sum -c SHA256SUMS
)

echo "PASS: backup checksum verified"

METADATA_SCHEMA_VERSION="$(awk -F= '$1 == "schema_version" { print $2 }' "$METADATA")"

[ "$METADATA_SCHEMA_VERSION" = "2" ]     || fail "unsupported filesystem backup metadata schema_version"

RECOVERY_SET_ID="$(awk -F= '$1 == "recovery_set_id" { print substr($0, index($0, "=") + 1) }' "$METADATA")"

[ -n "$RECOVERY_SET_ID" ]     || fail "backup metadata missing recovery_set_id"

[ "${#RECOVERY_SET_ID}" -le 128 ]     || fail "backup recovery_set_id exceeds 128 characters"

case "$RECOVERY_SET_ID" in
    *[!A-Za-z0-9._-]*)
        fail "backup recovery_set_id contains invalid characters"
        ;;
esac

if [ -n "$EXPECTED_RECOVERY_SET_ID" ] && [ "$EXPECTED_RECOVERY_SET_ID" != "$RECOVERY_SET_ID" ]; then
    fail "backup recovery_set_id does not match ADT_EXPECTED_RECOVERY_SET_ID"
fi

echo "recovery-set-id: $RECOVERY_SET_ID"
echo "PASS: backup recovery-set identity validated"

ARCHIVE_LIST="$(
    zstd -dc "$ARCHIVE"         | tar -tf -
)"

[ -n "$ARCHIVE_LIST" ]     || fail "backup archive is empty"

while IFS= read -r member; do
    [ -n "$member" ] || continue

    case "$member" in
        /*)
            fail "archive contains absolute path: $member"
            ;;
    esac

    case "/$member/" in
        */../*|*/..//*)
            fail "archive contains traversal path: $member"
            ;;
    esac

    case "$member" in
        data|data/*) ;;
        *)
            fail "archive contains unexpected root: $member"
            ;;
    esac
done <<EOF
$ARCHIVE_LIST
EOF

echo "PASS: archive member paths are bounded"

if [ "$MODE" = "dry-run" ]; then
    echo "DRY RUN: archive is valid"
    echo "DRY RUN: no filesystem content will be restored"
    echo "DRY RUN: execution requires ADT_RESTORE_QUIESCED=YES"
    exit 0
fi

[ "${ADT_RESTORE_QUIESCED:-}" = "YES" ]     || fail "execution requires ADT_RESTORE_QUIESCED=YES"

[ "$(id -u)" -eq 0 ]     || fail "restore execution requires root privileges"

if [ -e "$RESTORE_ROOT" ]; then
    [ -d "$RESTORE_ROOT" ]         || fail "restore root exists and is not a directory"

    [ ! -L "$RESTORE_ROOT" ]         || fail "restore root must not be a symbolic link"

    if find "$RESTORE_ROOT" -mindepth 1 -print -quit | grep -q .; then
        fail "restore root must be empty"
    fi
else
    mkdir -m 0700 "$RESTORE_ROOT"
fi

STAGING="$RESTORE_ROOT/staging"

[ ! -e "$STAGING" ]     || fail "restore staging path already exists"

mkdir -m 0700 "$STAGING"

cleanup() {
    if [ -d "$STAGING" ]; then
        rm -rf -- "$STAGING"
    fi
}

trap cleanup EXIT INT TERM

zstd -dc "$ARCHIVE"     | tar         --numeric-owner         --acls         --xattrs         -C "$STAGING"         -xf -

[ -d "$STAGING/data" ]     || fail "restored archive does not contain expected data root"

[ ! -L "$STAGING/data" ]     || fail "restored data root must not be a symbolic link"

RESTORED_UID="$(stat -c "%u" "$STAGING/data")"
RESTORED_GID="$(stat -c "%g" "$STAGING/data")"

if [ "$RESTORED_UID" != "10001" ] || [ "$RESTORED_GID" != "10001" ]; then
    fail "restored root ownership must be 10001:10001; found ${RESTORED_UID}:${RESTORED_GID}"
fi

echo "PASS: restore staging validated"
echo "staging-path: $STAGING"
echo "PROMOTION NOT PERFORMED"
echo "manual reviewed promotion is still required"

trap - EXIT INT TERM
