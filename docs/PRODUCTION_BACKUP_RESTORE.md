# ADT Production Backup and Restore Contract

## Scope

ADT production persistence spans two independent stores:

1. managed PostgreSQL/Supabase;
2. the complete persistent POSIX `ADT_DATA_DIR`.

Both are required for disaster recovery.

Neither store alone represents the complete ADT state.

## Filesystem backup boundary

The complete production tree is backed up:

`/srv/adt/data`

No application subdirectory is excluded merely because some content may appear
rebuildable.

The tree can contain authoritative or provenance-bearing state including:

- RAW market datasets and catalogs;
- derived datasets and manifests;
- immutable dataset snapshots;
- backtest artifacts;
- optimization evidence;
- PAPER configuration and persisted runtime artifacts;
- timeline and source-state bindings.

Selective backup would risk breaking provenance relationships.

## PostgreSQL boundary

PostgreSQL contains application and operational authority that is not represented
by the filesystem alone.

Examples include:

- operational mandates;
- approved paper-session profiles;
- capital authorizations;
- materializations;
- activations;
- run epochs and command history;
- collector epochs;
- leases and heartbeat state;
- capital eras and settlements;
- audit and administrative records.

Database recovery and filesystem recovery are therefore separate but coordinated
concerns.

## Supabase database backup strategy

Managed Supabase backup capability is the preferred production database recovery
mechanism when available for the selected production plan.

Point-in-Time Recovery may be used when the required recovery-point objective
justifies it.

A portable logical export is a secondary recovery mechanism and must follow the
reviewed Supabase backup procedure rather than assuming one generic `pg_dump`
contains every platform-owned recovery concern.

The logical backup design must explicitly account for:

- roles;
- schema;
- data;
- Supabase migration history;
- application changes involving managed schemas such as `auth` or `storage`.

No database restore is automated by the current delivery.

## ADT portable PostgreSQL backup

`ops/backup-production-postgres.sh` provides a portable logical backup of
ADT-owned PostgreSQL state.

It is not a complete Supabase-platform backup.

The portable backup contains:

- `pre-data.sql` for pre-data objects in the PostgreSQL `public` schema;
- `data.dump` for data in the PostgreSQL `public` schema;
- `post-data.sql` for post-data objects such as constraints, indexes and triggers;
- `SHA256SUMS`;
- `metadata.txt`.

The pre-data and post-data dumps are plain SQL so that the application schema
remains reviewable.

The data dump uses PostgreSQL custom format for controlled `pg_restore`.

The split restore order is:

1. pre-data;
2. data;
3. post-data.

This ordering is required because the ADT schema contains circular foreign-key
relationships. Restoring all constraints before data is not considered a valid
portable recovery procedure.

The portable ADT dump intentionally does not claim ownership of Supabase-managed
schemas or platform infrastructure such as:

- `auth`;
- `storage`;
- realtime/platform schemas;
- Supabase-managed roles;
- platform-owned migration metadata.

Those concerns remain part of managed Supabase recovery and any separately
reviewed Supabase-native export procedure.

The ADT logical dump therefore supplements, rather than replaces, managed
Supabase backup/PITR.

The script is dry-run by default and does not open a database connection unless
`--execute` is supplied.

Execution additionally requires:

- explicit `ADT_POSTGRES_BACKUP_URL`;
- explicit `ADT_BACKUP_ROOT`;
- `ADT_BACKUP_QUIESCED=YES`.

The backup script does not stop application processes and does not apply
migrations.

## Coordinated recovery-set identity

A coordinated filesystem/database recovery event uses one explicit:

`ADT_RECOVERY_SET_ID`

The same identifier must be supplied to both the filesystem backup and the ADT
portable PostgreSQL backup.

The identifier is stored inside each `metadata.txt` and is checksum-protected.

Accepted identifiers contain only:

- ASCII letters;
- digits;
- `.`;
- `_`;
- `-`.

Maximum length is 128 characters.

The identifier does not create transactional consistency by itself. It binds the
two artifacts to the same operator-declared recovery event while quiescence
provides the consistency boundary.

Backup timestamps remain observational metadata and must not be used as a
substitute for recovery-set identity.

Filesystem restore validates the embedded recovery-set identity. An operator may
also supply `ADT_EXPECTED_RECOVERY_SET_ID` to fail closed when a backup belongs
to a different recovery set.

## Coordinated consistency

PostgreSQL and `ADT_DATA_DIR` do not share one transactional snapshot boundary.

Until an online cross-store backup protocol is explicitly designed and tested,
a coordinated recovery set requires the ADT write topology to be stopped or
quiesced before the filesystem snapshot is taken.

Quiescence must cover processes capable of mutating either persistence domain.

A backup script must never stop services or forge administrative STOP commands
on its own.

The operator is responsible for establishing quiescence through the reviewed
operational procedure and then explicitly asserting that condition.

## Filesystem backup execution

`ops/backup-production-filesystem.sh` is dry-run by default.

Execution requires all of:

- explicit `--execute`;
- absolute `ADT_BACKUP_ROOT`;
- existing non-symlink `ADT_DATA_DIR`;
- `ADT_BACKUP_QUIESCED=YES`.

The quiescence variable is an operator assertion. It is not proof that processes
are actually stopped.

The script deliberately does not call Docker, systemd, application APIs or
database mutation commands.

## Backup destination

The backup destination must be outside `ADT_DATA_DIR`.

Backing up into the source tree is prohibited because it could create recursive
or internally inconsistent backups.

Production backup media should eventually be copied to independent durable
storage. That off-host destination is not selected by this delivery.

## Filesystem archive

The filesystem backup preserves the tree using TAR plus Zstandard compression.

The archive is created with numeric ownership information and filesystem
metadata needed by the POSIX storage contract where supported.

Every completed backup set contains:

- `adt-data.tar.zst`;
- `SHA256SUMS`;
- `metadata.txt`.

The backup is first written into a private staging directory and becomes visible
as a completed set only after checksum verification succeeds.

## Restore boundary

`ops/restore-production-filesystem.sh` implements only the filesystem restore
staging boundary.

It is dry-run by default.

Execution requires all of:

- explicit `--execute`;
- root privileges, because numeric ownership must be restored;
- absolute `ADT_RESTORE_BACKUP_SET`;
- absolute `ADT_RESTORE_ROOT`;
- `ADT_RESTORE_QUIESCED=YES`;
- a valid completed backup set;
- checksum verification;
- an empty restore root.

The script verifies archive member paths before extraction and rejects absolute,
traversing or unexpected archive roots.

Restore is extracted into a controlled staging directory.

The script deliberately refuses to restore directly into:

`/srv/adt/data`

and deliberately performs no automatic promotion from staging into production.

A reviewed future promotion procedure must still:

1. require a stopped/quiesced topology;
2. confirm backup checksums;
3. confirm the staged restored tree;
4. verify ownership and permissions;
5. establish ownership `10001:10001`;
6. validate filesystem manifests and important application contracts;
7. reconcile the database recovery point with the filesystem recovery set;
8. validate database and application readiness;
9. only then authorize production process startup.

Restore must fail closed.

## Recovery-point relationship

The database backup and filesystem archive should be associated with a common
operator recovery event.

A timestamp alone does not prove transactional equivalence between them.

Until stronger cross-store snapshot evidence exists, the recovery record must
state that the topology was quiesced before capture.

## Retention

This delivery does not invent a retention count or age.

Retention must be selected later using measured:

- database size;
- filesystem size and growth;
- backup duration;
- restore duration;
- storage cost;
- required RPO;
- required RTO.

Deletion of old backup sets must not be introduced before that policy exists.

## Verification

A backup is not considered verified merely because archive creation returned
success.

At minimum:

- archive checksum verification must pass;
- database backup availability must be independently confirmed;
- restore testing must later prove recoverability.

Phase 9 requires an actual restore test before production backup/restore work is
considered operationally accepted.

## Authority boundary

Backup and restore infrastructure does not grant:

- PAPER activation;
- capital authorization;
- operational START/RESUME;
- recommendation-driven activation;
- order authority;
- LIVE execution.

Backup tooling must never synthesize application authority.
