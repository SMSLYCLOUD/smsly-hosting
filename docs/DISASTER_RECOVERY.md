# Disaster Recovery

This is the operator runbook for restoring a Grid install from a failure. It covers the recommended backup schedule, the per-service and per-server restore procedures, the encryption key rotation procedure, the cloud-storage backup strategy, the manual `psql` restore procedure, the RPO / RTO targets, and the GDPR right-to-erasure procedure.

## Recommended Backup Schedule

The platform does not enforce a backup schedule. Operators are expected to configure their own. The recommended template is **6h + 24h + 7d** — a tiered schedule that balances RPO against storage cost.

| Schedule | Cadence | Retention | Storage | Purpose |
| --- | --- | --- | --- | --- |
| Frequent | Every 6 hours | 7 days | Local | Operational restore. Protects against accidental deletes and short-lived corruption. |
| Daily | Every 24 hours (03:00 UTC) | 14 days | Local + cloud destination | Standard daily snapshot. |
| Weekly | Every Sunday (04:00 UTC) | 90 days | Cloud destination | Long-term retention. The minimum required for SOC 2 / GDPR-style compliance. |

A service that has both the 6h and 24h schedules configured produces a maximum of 4 + 1 = 5 backups per day. With the 7-day and 14-day retentions, the local store holds ~150 backups per service. Operators with a 100-service fleet should budget for ~2 TB of local backup storage at a 50 MB / backup average.

### Schedule Templates

For each service, create two `BackupSchedule` rows:

```bash
# 6h, 7d retention, local only
curl -sS -X POST http://localhost:8000/api/v1/backup-schedules/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "cron": "0 */6 * * *",
    "retention_count": 28,
    "enabled": true
  }'

# 24h, 14d retention, replicated to cloud
curl -sS -X POST http://localhost:8000/api/v1/backup-schedules/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "cron": "0 3 * * *",
    "retention_days": 14,
    "destination": "..."
  }'
```

For server-wide backups, create a `BackupSchedule` with `service=null` and an admin token.

## Restore Procedure: Single Service

Use the per-service restore path when a single service has been corrupted, deleted, or rolled back to a known-bad revision.

### Prerequisites

- The service's `ServiceBackup` is still in the local or cloud store.
- The `BACKUP_ENCRYPTION_KEY` that was active at backup time is in `.env` (use the multi-key fallback if needed — see [Encryption Key Rotation](#encryption-key-rotation)).
- The service's current state is captured in a `PRE_TRANSFER` snapshot (this is automatic on the restore path).

### Steps

1. **Identify the backup.** List backups for the service:

   ```bash
   curl -sS "http://localhost:8000/api/v1/backups/?service_id=9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21" \
     -H "Authorization: Token $SMSLY_TOKEN" | jq '.[0:5]'
   ```

2. **Confirm the choice.** Open the service detail page in the UI. Verify the backup's `created_at` and `trigger` (e.g. `MANUAL`, `SCHEDULED`, `PRE_TRANSFER`). A `PRE_TRANSFER` snapshot is the pre-restore snapshot from a previous restore — useful for rolling back a botched restore.

3. **Trigger the restore.** Pass `confirm=true` explicitly. The endpoint refuses any other value:

   ```bash
   curl -sS -X POST "http://localhost:8000/api/v1/backups/3e4f5a6b-7c8d-9e0f-1a2b-3c4d5e6f7a8b/restore/" \
     -H "Authorization: Token $SMSLY_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"confirm": true}'
   ```

4. **The platform captures a `PRE_TRANSFER` snapshot** of the current state with `raise_on_snapshot_failure=True`. If the snapshot fails, the restore is aborted. The operator sees a clear error and the service is left in its current state.

5. **The restore task runs.** It:
   - Decrypts the archive.
   - Stops the service's running container.
   - Unpacks the archive into the service's volume.
   - Restores the `Service` row's metadata (env vars, ports, custom domains).
   - Triggers a fresh deployment of the restored state.
   - Removes the decrypted scratch directory.

6. **Verify the restore.** Once the deployment reaches `ACTIVE`, hit the service's public domain and verify the data. If the restore was bad, restore the `PRE_TRANSFER` snapshot that was just captured — it is the snapshot of the pre-restore state, which is now your "known-good" reference.

7. **Audit-log the manual restore.** The restore is recorded in `AuditLog` with `actor=<user>`, `action=SERVICE_RESTORE`, `target=<service>`, `metadata={"backup_id": "..."}`. The chain is hash-linked and immutable.

### Caveat: in-flight writes during the restore

The restore stops the container before unpacking. Any in-flight writes from the moment of the stop to the moment the new container starts accepting traffic are lost. For most workloads this is a few seconds; for high-throughput services, the RPO is the time of the last backup, not the time of the restore.

## Restore Procedure: Full Server

Use the full-server restore path when the platform's database has been corrupted, lost, or is unrecoverable (e.g. disk failure on the controller). This is the nuclear option — the platform is offline for the duration of the restore.

### Prerequisites

- A recent `ServerBackup` in the local or cloud store.
- The `BACKUP_ENCRYPTION_KEY` that was active at backup time is in `.env`.
- The operator has access to the host (SSH) to run `psql` manually.

### Caveat: DB dump is NOT auto-restored

The full-server restore does **not** auto-restore the database. The platform's `restore_server_backup` task only restores the platform's `.env` and the Caddy / Traefik config. The database must be restored manually via `psql`. This is a deliberate safety measure — auto-restoring the database would clobber any newer audit log rows, recent deployments, or recent service updates that have happened since the backup.

See [Manual `psql` Restore](#manual-psql-restore) for the procedure.

### Steps

1. **Stop the platform.** `docker compose -f docker-compose.prod.yml down` on the controller.

2. **Identify the server backup.**

   ```bash
   curl -sS "http://localhost:8000/api/v1/server/backups/" \
     -H "Authorization: Token $SMSLY_ADMIN_TOKEN" | jq '.[0:5]'
   ```

   If the platform is down, the local archives are still readable from `/var/lib/grid/server-backups/`. List them with `ls -lt`.

3. **Restore the platform's `.env`.** This is the easy part — the platform's restore path does it:

   ```bash
   curl -sS -X POST "http://localhost:8000/api/v1/server/backups/<backup-id>/restore/" \
     -H "Authorization: Token $SMSLY_ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"confirm": true, "restore_env": true, "restore_config": true}'
   ```

   The endpoint refuses to restore the database (`restore_db` is a separate, manual step).

4. **Restore the database manually.** See [Manual `psql` Restore](#manual-psql-restore).

5. **Bring the platform back up.** `docker compose -f docker-compose.prod.yml up -d`.

6. **Verify the platform state.** Open the dashboard, confirm the services are listed, and trigger a deployment for each service. The deployments re-attach the volumes (which were preserved on the host's filesystem) to the restored services.

7. **Re-issue SSL certs if needed.** If the backup predates a hostname change, the new Caddy config will trigger a cert re-issue. This is automatic.

### Volume State

Volume data lives on the host's filesystem, not in the database. A full-server restore does **not** touch volumes. The restored services re-attach the existing volumes on the next deploy. This is by design — restoring a database should not destroy the operator's actual user data.

The trade-off: if the volume was the thing that was corrupted (not the database), a full-server restore is the wrong tool. Use a per-service restore from a service backup instead.

## Encryption Key Rotation

The `BACKUP_ENCRYPTION_KEY` should be rotated periodically (every 12 months is a common cadence; more often for high-compliance installs). The rotation does **not** retroactively re-encrypt old backups — instead, the platform supports a multi-key fallback at decrypt time.

### Multi-Key Support

The `BACKUP_ENCRYPTION_KEY` env var accepts a comma-separated list. The first key in the list is used for **writes** (new backups). All keys are tried, in order, on **reads** (decrypting old backups).

```
BACKUP_ENCRYPTION_KEY=new-key-32-bytes-base64,old-key-32-bytes-base64
```

The encrypt-on-write path picks `os.environ['BACKUP_ENCRYPTION_KEY'].split(',')[0]`. The decrypt path tries each key in order, falling through on `InvalidToken` errors. The platform never mixes keys within a single archive.

### Procedure

1. **Generate a new key.**

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. **Append the new key to the env var.** Edit the platform `.env`:

   ```
   BACKUP_ENCRYPTION_KEY=new-key,old-key
   ```

3. **Restart the backend.** The new key is read at boot.

4. **Verify the new key is in use.** Trigger a manual backup and check its `metadata.key_fingerprint` (the first 8 bytes of the SHA-256 of the key, base64-encoded). The fingerprint should match the new key's fingerprint, not the old one.

5. **After 90 days, drop the old key.** Once all old-key backups have aged out of the retention window, remove the old key from the list. New backups continue with the new key; reads no longer try the old key.

### What Does NOT Happen on Rotation

- Old archives are **not** re-encrypted in place.
- The retentor is **not** re-run — the old-key archives are still subject to their original retention policy.
- Audit log rows are **not** updated.

If the operator needs to re-encrypt in place (e.g. to drop the old key immediately, not in 90 days), the procedure is:

1. Restore the old-key archive to a new service.
2. Trigger a manual backup of the restored service. The new backup is encrypted with the current (new) key.
3. Delete the old-key archive.
4. Repeat for every backup that uses the old key.

This is intentionally a manual process. A batch re-encrypt tool exists in `apps/storage/management/commands/reencrypt_backups.py` but is **not** wired into the rotation procedure — it is a one-shot tool for emergencies.

## Cloud Storage Destination Backup Strategy

The local backup store is on the controller's disk. If the controller's disk fails, the local store is gone. To protect against this, replicate backups to a `CloudStorageDestination` (see [docs/STORAGE.md](STORAGE.md#cloud-storage-destinations)).

### Recommended Pattern

For each service, configure two schedules:

1. **Local-only, high frequency.** Every 6h, retain 28 backups (7 days). Cheap, fast to restore.
2. **Cloud-replicated, low frequency.** Every 24h, retain 14 backups. Slower restore (network-bound) but off-host.

The cloud-replicated schedule uses the `destination` field on `BackupSchedule` to specify the `CloudStorageDestination`. After the local write, the schedule task uploads the archive to the destination. The upload is verified with a `HeadObject` after the `PUT` to confirm the object's size matches.

### Cost

For a 50 MB average backup and 14-day retention on a 100-service fleet:

- Local: 100 services × 4 backups/day × 50 MB × 14 days = 280 GB. SSDs handle this comfortably.
- Cloud: 100 services × 1 backup/day × 50 MB × 14 days = 70 GB. On R2, this is ~$0.04/month egress-free.

The cost is dominated by egress, not storage. Operators who do not need off-host backups can skip the cloud schedule entirely.

## Manual `psql` Restore

The full-server restore does not auto-restore the database. The operator must restore the database manually.

### Steps

1. **Locate the `pg_dump` file** in the server backup. The `ServerBackup` archive contains a file at `db/dump.custom`. The full path after decryption is `/tmp/smsly-decrypted-<uuid>/db/dump.custom`.

2. **Stop the platform's database container.** Keep the platform's backend down (do not start it):

   ```bash
   docker compose -f docker-compose.prod.yml stop db
   ```

3. **Drop and recreate the database.** This is destructive — the operator must confirm:

   ```bash
   docker compose -f docker-compose.prod.yml exec db psql -U postgres -c "DROP DATABASE grid;"
   docker compose -f docker-compose.prod.yml exec db psql -U postgres -c "CREATE DATABASE grid;"
   ```

4. **Restore the dump.** The dump is in PostgreSQL's custom format (`pg_dump -Fc`). Use `pg_restore`:

   ```bash
   docker compose -f docker-compose.prod.yml exec -T db \
     pg_restore -U postgres -d grid --no-owner --role=postgres \
     /var/lib/grid/server-backups/<backup-id>/db/dump.custom
   ```

   The `-T` flag is required because `exec` does not allocate a TTY. The `--no-owner` flag is required because the dump's owner user may not exist on the new install.

5. **Verify the restore.**

   ```bash
   docker compose -f docker-compose.prod.yml exec db psql -U postgres -d grid \
     -c "SELECT count(*) FROM services_service;"
   ```

   The count should match the expected number of services for the install.

6. **Start the platform's backend.** `docker compose -f docker-compose.prod.yml up -d backend`. The backend will run migrations on top of the restored schema (no-op for migrations that have already been applied).

7. **Clean up the decrypted scratch.** The platform's Celery beat task will clean it up within 15 minutes. To force-clean, `rm -rf /tmp/smsly-decrypted-<uuid>`.

### Common Issues

- **"role does not exist"** — the dump includes role definitions. Use `--no-owner` and `--role=postgres` as above.
- **"could not connect to server"** — the database container is not running. Check `docker compose ps` and start it with `docker compose up -d db`.
- **The restore is slow (>30 min for a large dump).** The custom format compresses on disk; the restore is single-threaded by default. Use `pg_restore -j 4` to parallelize, but note that parallel restore can corrupt the dump if the original was made with `--serializable-deferrable` constraints. For a 1 GB dump, parallel restore takes ~10 minutes.

## RPO / RTO Targets

| Failure scenario | RPO (data loss) | RTO (recovery time) |
| --- | --- | --- |
| Accidental delete of a single service | Last 6h backup (max 6h) | 5–15 minutes (per-service restore) |
| Corruption of a single service's volume | Last backup (max 6h for local, max 24h for cloud) | 5–15 minutes (per-service restore) |
| Controller disk failure | Last backup (max 6h for local — lost on the same disk; max 24h for cloud) | 30–90 minutes (full-server restore + manual `psql`) |
| Database corruption (DB container is alive, schema is bad) | Last backup (max 6h local, max 24h cloud) | 30–60 minutes (full-server restore + manual `psql`) |
| Full region outage | Last backup replicated to another region (custom config) | Hours (requires cross-region replication, not built-in) |

The RTOs assume the operator has the `BACKUP_ENCRYPTION_KEY` and can run the restore commands. The RPOs assume the 6h + 24h schedule is in place. With a 24h-only schedule, all RPOs double.

## GDPR Right-to-Erasure

When a user is deleted, the platform runs `purge_user_backups_task` to remove the user's data. The task is registered as a Celery signal handler on the `User` model's `post_delete` signal.

### What the Task Does

1. **Cascade service backups.** Every `ServiceBackup` row where `service__owner=user`. The local archive is unlinked. The cloud-stored object (if any) is deleted from the destination.

2. **Cascade server backups.** Every `ServerBackup` row that contains the user's data. The server backup is rewritten to remove the user's services, audit log rows, and addons. The rewriter is a one-pass operation — the original archive is decrypted, the user's rows are filtered out, the archive is re-encrypted with the current key, and the old archive is replaced.

3. **Cascade destinations.** Every `CloudStorageDestination` row where `owner=user`. The destination's bucket is NOT deleted (the bucket is shared with other users' destinations in some configurations). Only the objects under the destination's `path_prefix/<user_id>/` prefix are deleted.

4. **Cascade schedules.** Every `BackupSchedule` row where `service__owner=user`. Schedules are deleted, not just disabled.

5. **Cascade audit log.** The user's audit log rows are NOT deleted. Instead, the rows' `metadata.user_id` and `actor_user_id` are set to `null`. The rows themselves are preserved for the audit chain's hash integrity. Operators who need a stronger erasure must re-build the entire audit chain, which is out of scope for the platform.

6. **Cascade addons.** Every `Addon` row where `service__owner=user`. The addon's data is purged (PostgreSQL drop database, Redis flush, etc., depending on the addon's type).

### Procedure for the Operator

When a user requests erasure (typically via a support email or a "delete my account" UI flow):

1. **Verify the request.** GDPR requires that the request be verifiable (e.g. the user can prove they own the account). The platform's account deletion flow already does this via email confirmation.

2. **Trigger the deletion.** Use the platform's user deletion endpoint or the Django admin:

   ```bash
   docker exec smsly-hosting-backend-1 python manage.py shell \
     -c "from django.contrib.auth import get_user_model; \
         U = get_user_model(); \
         u = U.objects.get(email='user@example.com'); \
         u.delete()"
   ```

   The `post_delete` signal fires `purge_user_backups_task` automatically.

3. **Wait for the task to complete.** The task logs progress to the platform's Celery worker. For a user with 100 service backups across 5 destinations, the task takes 10–30 minutes.

4. **Verify the erasure.**

   ```bash
   docker exec smsly-hosting-backend-1 python manage.py shell \
     -c "from apps.storage.models import ServiceBackup, CloudStorageDestination, BackupSchedule; \
         print('ServiceBackups:', ServiceBackup.objects.filter(service__owner_email='user@example.com').count()); \
         print('Destinations:', CloudStorageDestination.objects.filter(owner_email='user@example.com').count()); \
         print('Schedules:', BackupSchedule.objects.filter(service__owner_email='user@example.com').count())"
   ```

   All counts should be 0.

5. **Audit-log the erasure.** The `purge_user_backups_task` writes a `USER_PURGED` row to `AuditLog` with the user's email (redacted to a hash for the public log) and the count of purged records. The chain is hash-linked.

### Edge Cases

- **User owns a destination that is also used by other users.** The destination is not deleted; only the user's prefix is purged. Other users' objects are untouched.
- **User is referenced in a server backup that is mid-write.** The task waits for the write to complete before processing. There is a 10-minute timeout — after that, the task aborts and the operator must retry.
- **The user has active deployments.** The deployments are NOT cancelled. The platform refuses to delete a service with an active deployment; the operator must cancel the deployment first.
- **Cloud destination is unreachable.** The task retries 3 times with exponential back-off. After 3 failures, the orphan objects are logged to `AuditLog` and the operator is expected to clean them up manually.

## Limitations

- **No continuous replication.** The platform's backup model is snapshot-based, not streaming. An outage that happens 1 second after a snapshot can still lose 6h of data.
- **No cross-region failover.** The full-server restore is a manual procedure. Operators who need cross-region failover should run a multi-node install and use the platform's mesh to replicate.
- **No automatic `psql` restore.** A full-server restore is incomplete without the manual `psql` step. Operators who need automatic restore should use a managed PostgreSQL with point-in-time recovery.
- **The retentor is single-threaded.** A large backup set (>10 000 archives) takes hours to retend. Operators with very large fleets should lower the retention counts.
- **Encryption key rotation is a manual procedure.** The platform does not auto-rotate `BACKUP_ENCRYPTION_KEY`. Set a calendar reminder.
- **`purge_user_backups_task` does not erase the audit log.** The audit chain's hash integrity is preserved at the cost of a soft delete on the user references. Operators with stricter requirements must rebuild the chain.
- **No cross-platform backup verification.** The platform does not verify that a backup can be restored at the time of write. A corrupt backup is detected on the next restore attempt, not at write time. Operators who need write-time verification should run a scheduled test-restore in a sandbox.
