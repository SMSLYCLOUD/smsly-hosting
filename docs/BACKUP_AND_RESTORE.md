# Backups and Restore

## Backup Process
- Service backups capture environment variables, active metadata, and volume state.
- Server backups capture the core PostgreSQL database, platform configuration, and orchestrate service backups.
- Secrets within environment variables are masked during metadata snapshot generation (`is_secret` check).
- **Retention:** When automatic pruning runs (`BACKUP_RETENTION_COUNT`), the system is guaranteed to *never* delete the last `COMPLETED` backup regardless of age.

## Restore Process
- Restores are highly destructive. They replace the current active file system and configuration with the archive contents.
- **Safety Gate:** Restoring a backup requires explicit confirmation (`confirm=True` payload) to prevent accidental overwrites.
- **Pre-Restore Snapshot:** Before a restore initiates, a `PRE_TRANSFER` snapshot of the target service is captured to prevent catastrophic data loss if the restore archive is corrupt.
- **Path Traversal:** Archives are strictly evaluated against path traversal attacks (`..` or `/` prefixes).
