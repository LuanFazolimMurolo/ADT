# ADT Production Storage Contract

## Canonical root

Production uses one persistent POSIX filesystem root:

`ADT_DATA_DIR=/srv/adt/data`

The API, market-data supervisor and PAPER-run supervisor must observe the same
underlying filesystem.

## Container identity

Backend production containers run as:

- UID: `10001`
- GID: `10001`
- user: `adt`

The production host storage root must be owned by the same numeric UID/GID.

Numeric identity is intentional because bind mounts preserve host filesystem
ownership and do not inherit image-layer ownership.

## Bootstrap

Before starting ADT production services, an operator runs:

`sudo ./ops/bootstrap-production-storage.sh`

The bootstrap creates and validates:

- `/srv/adt/data`
- `/srv/adt/data/market`

Both directories use mode `0750` and ownership `10001:10001`.

Neither storage root may be a symbolic link.

## Application-owned layout

Subdirectories and canonical artifacts below the storage root remain owned by
the application contracts.

Examples include market datasets, derived datasets, immutable snapshots,
backtest artifacts, optimization evidence and PAPER artifacts.

Deployment automation must not invent, rename or reorganize those internal
paths independently from application code.

## Atomicity and locking

ADT filesystem writers rely on POSIX filesystem semantics including:

- atomic rename/replacement;
- advisory locking;
- directory creation;
- fsync behavior;
- hard links for snapshot materialization.

Therefore the production storage must be a local or equivalent POSIX filesystem
that preserves those semantics.

Object storage is not a drop-in replacement for `ADT_DATA_DIR`.

## Backup boundary

The complete `/srv/adt/data` tree is the filesystem backup boundary.

Backups must preserve:

- relative paths;
- file contents;
- permissions where operationally required;
- immutable artifacts and manifests together;
- consistency between linked snapshot artifacts.

Database backup and filesystem backup are separate concerns and must both be
covered by the Phase 9 backup/restore delivery.

## Restore boundary

A filesystem restore must target a stopped/quiesced ADT topology unless a later
validated online-backup protocol explicitly permits otherwise.

After restore, ownership must resolve to `10001:10001` before ADT runtime
processes start.

## Safety

Storage bootstrap and restore do not grant:

- PAPER activation;
- capital authorization;
- LIVE authority;
- exchange-account authority.

They only establish persistent filesystem infrastructure.
