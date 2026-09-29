#!/bin/sh
set -eu

ADT_STORAGE_ROOT="${ADT_STORAGE_ROOT:-/srv/adt/data}"
ADT_UID="${ADT_UID:-10001}"
ADT_GID="${ADT_GID:-10001}"

case "$ADT_STORAGE_ROOT" in
    /*) ;;
    *)
        echo "ERROR: ADT_STORAGE_ROOT must be absolute" >&2
        exit 1
        ;;
esac

if [ -L "$ADT_STORAGE_ROOT" ]; then
    echo "ERROR: storage root must not be a symlink: $ADT_STORAGE_ROOT" >&2
    exit 1
fi

if [ -e "$ADT_STORAGE_ROOT" ] && [ ! -d "$ADT_STORAGE_ROOT" ]; then
    echo "ERROR: storage root exists but is not a directory: $ADT_STORAGE_ROOT" >&2
    exit 1
fi

install -d -m 0750 -o "$ADT_UID" -g "$ADT_GID" "$ADT_STORAGE_ROOT"

MARKET_ROOT="$ADT_STORAGE_ROOT/market"

if [ -L "$MARKET_ROOT" ]; then
    echo "ERROR: market root must not be a symlink: $MARKET_ROOT" >&2
    exit 1
fi

if [ -e "$MARKET_ROOT" ] && [ ! -d "$MARKET_ROOT" ]; then
    echo "ERROR: market root exists but is not a directory: $MARKET_ROOT" >&2
    exit 1
fi

install -d -m 0750 -o "$ADT_UID" -g "$ADT_GID" "$MARKET_ROOT"

ROOT_UID="$(stat -c %u "$ADT_STORAGE_ROOT")"
ROOT_GID="$(stat -c %g "$ADT_STORAGE_ROOT")"
MARKET_UID="$(stat -c %u "$MARKET_ROOT")"
MARKET_GID="$(stat -c %g "$MARKET_ROOT")"

if [ "$ROOT_UID" != "$ADT_UID" ] || [ "$ROOT_GID" != "$ADT_GID" ]; then
    echo "ERROR: storage root ownership mismatch" >&2
    exit 1
fi

if [ "$MARKET_UID" != "$ADT_UID" ] || [ "$MARKET_GID" != "$ADT_GID" ]; then
    echo "ERROR: market root ownership mismatch" >&2
    exit 1
fi

echo "ADT production storage ready:"
echo " root=$ADT_STORAGE_ROOT"
echo " owner=$ADT_UID:$ADT_GID"
echo " mode=0750"
