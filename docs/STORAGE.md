# Storage, Volumes & Backups

Grid's storage subsystem covers per-service file system mounts, off-platform cloud storage destinations, and the full backup / restore pipeline. The backup system is the only place where user data is encrypted at rest by default — operators are expected to leave `BACKUP_REQUIRE_ENCRYPTION` on for production installs.

## Overview

There are three loosely-coupled layers:

1. **Volumes** — Per-service file system mounts. Backing store is the host's Docker volume directory (`/var/lib/docker/volumes/<id>/_data` by default). Operations are: browse, read, write, delete, download, mkdir, upload.
2. **Cloud storage destinations** — S3-compatible object storage (R2, S3, MinIO, B2, DO Spaces, Wasabi). Used as a backup target. Each user can register one or more destinations; the platform uses them in the order configured.
3. **Backups** — Service-level and server-level archives. Encrypted at rest with Fernet. Stored locally and (optionally) replicated to a cloud destination. Restores are gated by a confirmation payload and a pre-restore snapshot.

All three are reachable from the same `/api/v1/...` URL space and share the same `AuditLog` chain.

## Volumes

A volume is a directory mount on a running service. The platform surfaces it through the API and the file browser UI. Volumes are scoped to a service and survive container restarts; they **are** removed when a service is deleted (`on_delete=models.CASCADE`).

### Mount Model

Each service has zero or more `Volume` rows. A row defines a named volume with a container mount path and size:

| Field | Type | Notes |
| --- | --- | --- |
| `name` | CharField(255) | Volume name (used as Docker volume name). |
| `mount_path` | CharField(255) | Container path where the volume is mounted. |
| `size_gb` | IntegerField | Size in GB (default 1, min 1, max 1000). |
| `service` | FK(Service) | CASCADE on delete. |

Volumes are stored as Docker volumes on the host that runs the service (`/var/lib/docker/volumes/<id>/_data` by default).

### Operations

| Operation | Endpoint | Notes |
| --- | --- | --- |
| List entries | `GET /api/v1/services/{id}/volumes/` | Recursive flag. |
| Read file | `GET /api/v1/services/{id}/volumes/read/` | Bounded by `MAX_VOLUME_READ_BYTES` (default 8 MB). Larger files return HTTP 413 with a `?download=true` redirect. |
| Write file | `POST /api/v1/services/{id}/volumes/write/` | Body is the file contents. Bounded by `MAX_VOLUME_WRITE_BYTES` (default 32 MB). |
| Delete | `DELETE /api/v1/services/{id}/volumes/delete/` | Single file or recursive directory. Path traversal blocked. |
| Mkdir | `POST /api/v1/services/{id}/volumes/mkdir/` | Idempotent. |
| Download | `GET /api/v1/services/{id}/volumes/download/` | Returns a signed URL (see [Signed URLs](#signed-download-urls)). |
| Upload | `POST /api/v1/services/{id}/volumes/upload/` | Multipart form. Bounded by `MAX_VOLUME_UPLOAD_BYTES` (default 100 MB). |

Path traversal is blocked at the controller: the resolved path is checked against the volume root and any `..` or absolute prefix is rejected with HTTP 400.

### Browser

The `/services/{id}/volumes/` frontend page renders a tree view. Uploads use a chunked transfer so 100 MB files do not need to fit in browser memory. The page is reachable only by the service owner and platform admins.

## Cloud Storage Destinations

A `CloudStorageDestination` is an S3-compatible bucket that the platform can use as a backup target. Supported providers:

| Provider | Endpoint scheme | Notes |
| --- | --- | --- |
| Cloudflare R2 | `https://<account>.r2.cloudflarestorage.com` | No egress fees. |
| AWS S3 | `https://s3.<region>.amazonaws.com` | Standard AWS auth. |
| MinIO (self-hosted) | `http://<host>:9000` (or `https://`) | Path-style addressing. |
| Backblaze B2 | `https://s3.<region>.backblazeb2.com` | S3-compatible. |
| DigitalOcean Spaces | `https://<region>.digitaloceanspaces.com` | |
| Custom Storage VPS | User-provided endpoint | Any S3-compatible endpoint. |
| Wasabi | `https://s3.<region>.wasabisys.com` | |

The endpoint URL is validated at `clean()` time. The endpoint scheme must be `https` **unless** the host is `localhost`, an RFC 1918 range, or `.internal` — in those cases `http` is permitted (the assumption is that the destination is reachable over a private network and HTTPS is not terminated by a CA-signed cert). Public hosts with `http://` are rejected.

### Owner Scoping

Destinations are scoped to the service they are attached to (`service=FK(Service, null=True, blank=True)`). The `CloudStorageViewSet` enforces service-scoped reads; admin users see all destinations across the platform. The viewset uses `get_queryset()` to filter by `service__owner` for non-admins, and the serializer hides the `secret_key` field on the response (it is `EncryptedCharField`).

A bug fix shipped in Batch C tightens the cross-tenant ACL: an unprivileged user can no longer list, read, or write to a destination owned by another user, even by guessing the destination's UUID. The check is in `CloudStorageViewSet.get_object()` and is unit-tested.

### Credentials

Each destination stores:

| Field | Type | Notes |
| --- | --- | --- |
| `access_key` | `EncryptedCharField` | Never returned in API responses. |
| `secret_key` | `EncryptedCharField` | Never returned in API responses. |
| `bucket` | string | The bucket name. |
| `region` | string | Optional. Used for AWS Signature v4. |
| `endpoint` | CharField(500) | Validated per the table above. |
The platform uses the `boto3` library to PUT and GET objects. The bucket must exist; the platform does not auto-create buckets.

## Backup System

Backups are first-class resources. There are two scopes:

- **Service backups** — A snapshot of a single service's volumes, environment variables, and active deployment metadata.
- **Server backups** — A snapshot of the platform's PostgreSQL database, all `Service` rows, all `CloudStorageDestination` rows, all `AuditLog` rows, and the platform `.env`.

Service backups can be scheduled (see [Schedules](#schedules)). Server backups are typically scheduled on a 24h cadence by the install script.

### Encryption

Backups are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). The master key is `BACKUP_ENCRYPTION_KEY` in the platform `.env`. The flag `BACKUP_REQUIRE_ENCRYPTION` (default `True` when `DEBUG=False`) refuses to write an unencrypted backup. The encryption is symmetric: the same key is required to decrypt.

The encrypted envelope is:

```
{
  "version": 1,
  "ciphertext": "<base64 Fernet token>",
  "compression": "gzip",
  "created_at": "2026-06-12T15:23:11Z",
  "service_id": "9c8b4b1a-..."
}
```

The Fernet token wraps a gzipped tarball. Decryption requires the exact `BACKUP_ENCRYPTION_KEY` that was active at backup time. **Rotating the key does not retroactively re-encrypt old backups** — see [Multi-Key Support](#multi-key-support) for the recovery path.

### Multi-Key Support

When the operator rotates `BACKUP_ENCRYPTION_KEY`, the new key can be set in `.env` as a comma-separated list. The decryptor tries each key in order, falling through on `InvalidToken` errors. This allows old backups to remain readable after rotation. The first key in the list is used for new backups.

Example:

```
BACKUP_ENCRYPTION_KEY=new-key-32-bytes-base64,old-key-32-bytes-base64
```

The new key is used for writes; the old key is tried as a fallback on read. The platform never mixes keys within a single archive.

### Schedules

A `BackupSchedule` row is a CRON-like trigger that creates a `ServiceBackup` (or `ServerBackup`) on a schedule. The fields are:

| Field | Type | Notes |
| --- | --- | --- |
| `service` | FK | `NULL` for server-wide schedules. |
| `cron_expression` | string | Standard 5-field cron. |
| `retention_days` | int | Optional. Per-schedule override. |
| `s3_bucket` | string | Optional. S3 bucket name for cloud replication. |
| `s3_region` | string | Optional. S3 region. |
| `s3_endpoint` | string | Optional. S3-compatible endpoint URL. |
| `s3_access_key` | EncryptedCharField | Optional. S3 access key. |
| `s3_secret_key` | EncryptedCharField | Optional. S3 secret key. |
| `enabled` | bool | Default `True`. |

> **Note**: Cloud storage credentials are stored inline on the schedule (not as a FK to `CloudStorageDestination`). The `CloudStorageDestination.apply_to_schedule()` method copies S3 config into these inline fields.

The Celery beat task walks `BackupSchedule.objects.filter(enabled=True)` every minute, computes which schedules are due (`croniter.match(cron, now)`), and enqueues a `run_backup_schedule` task per hit. The task is idempotent — if a schedule fires twice in the same minute, only one backup is created (the second hit is a no-op).

### Retention

The platform keeps the last `BACKUP_RETENTION_COUNT` backups by default (env, default 20). Per-schedule `retention_count` and `retention_days` override this. The retentor runs after every backup write and:

1. Sort backups by `created_at` descending.
2. Drop any older than `retention_days` (if set).
3. Drop everything past the `retention_count`-th position.
4. **Never** drop the last `COMPLETED` backup, regardless of age.

The "never drop the last `COMPLETED`" rule is an explicit safety net. Operators who want a tighter retention set `retention_count=1` and accept that they always have at most one restorable backup.

### Restore Flow

A restore is highly destructive: it replaces the service's active state with the archive's contents. To prevent accidental overwrites, the platform has two gates:

1. **Confirmation gate** — The endpoint requires `confirm=True` in the body. Any other value returns HTTP 400.
2. **Pre-restore snapshot** — Before the restore begins, the platform captures a `ServiceBackup` of the **current** state with `trigger=PRE_TRANSFER`. If the restore archive is corrupt, the operator can roll back to the pre-restore snapshot.

The `restore_backup` task passes `raise_on_snapshot_failure=True` to the snapshot task. If the snapshot cannot be captured (e.g. the volume is too large to back up within the IO budget), the restore is aborted and the operator sees a clear error. There is no silent failure path.

### Decrypted Backup File Cleanup

Decrypted backups are written to `/tmp/smsly-decrypted-<uuid>/` with the directory mode set to `0o700` and individual file modes set to `0o600`. The decryptor is the only process that writes to that directory; no other user on the host can read it.

The directory is removed after the restore completes (success or failure) via `shutil.rmtree`. A periodic cleanup task (Celery beat, every 15 minutes) scans `/tmp` for orphaned `smsly-decrypted-*` directories older than 1 hour and removes them. The 1-hour window is intentional: a restore that takes longer than expected (e.g. a 30 GB volume) does not have its scratch directory yanked mid-restore.

### Signed Download URLs

Backups are downloadable via two paths:

1. **Owner-authenticated** — `GET /api/v1/backups/{id}/download/` requires the backup owner or a platform admin. The response is a 302 to a presigned S3 URL (or a stream of the file, if local).
2. **Signed** — `GET /api/v1/backups/{id}/download/?signed=<token>`. The token is a JWT signed with `BACKUP_ENCRYPTION_KEY` (HS256, 1-hour expiry). The token is generated on demand by the backup owner and can be shared with an off-platform recipient (e.g. an audit reviewer).

A legacy `?token=<token>` parameter is **no longer accepted** (removed in Batch C). The old parameter accepted a server-issued download token with no expiry; the new `?signed=` parameter requires the JWT and is bound to the backup's UUID. Operators with old share-links need to regenerate them.

### GDPR: `purge_user_backups_task`

When a user is deleted, the platform runs `purge_user_backups_task` (see [docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md#gdpr-right-to-erasure)). The task cascades through:

1. All `ServiceBackup` rows where `service__owner=user`. Each row's local archive is unlinked.
2. All `ServerBackup` rows where the user owned any service referenced in the archive's `services` field. The server-backup archive is rewritten to remove the user's data (the platform cannot retroactively un-encrypt the ciphertext, so a new archive is generated with the redacted payload — see [Server Backups](#server-backups) below).
3. All `CloudStorageDestination` rows where `owner=user`. The platform deletes each object under the destination's `path_prefix/<service_id>/` prefix.
4. All `BackupSchedule` rows where `service__owner=user`. Schedules are deleted, not just disabled.
5. The task is idempotent — re-running it on a user that no longer exists is a no-op.

## Server Backups

A `ServerBackup` is a full-platform snapshot. It contains:

- A `pg_dump` of the platform's PostgreSQL (in custom format, compressed).
- A copy of the platform's `.env` (with secrets redacted — see below).
- A copy of the Caddy / Traefik config.
- A JSON manifest of all `Service` rows and their `cloud_storage_destination` UUIDs.

The `.env` redaction is a defense-in-depth measure: the archive is encrypted at rest, but the operator may want to inspect the archive on a workstation without the key. The redactor strips values whose key matches `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `*_DSN`, `*_URL` (when the value contains a credential). The redacted values are replaced with the literal string `••••••••`.

The Caddy / Traefik config is included verbatim — it does not contain secrets, only domain / route definitions.

Server backups are typically 10–500 MB depending on the size of the `AuditLog` table. They are not designed for high-frequency backup. The recommended cadence is 24h, with a 7-day retention.

### Restore Caveat

A server-backup restore drops the entire platform database and re-creates it from the `pg_dump`. The platform's running services are **not** auto-restored; the database is the only state that is rewritten. The operator must manually restart the platform (`docker compose -f docker-compose.prod.yml up -d`) and then re-trigger deployments for each service. Volume data is preserved (it lives on the host's filesystem, not in the database) and is re-attached to the restored services automatically.

For a full server restore, see [docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md#restore-procedure-for-a-full-server).

## API Reference

All endpoints are mounted under `/api/v1/`. Authentication is session- or token-based; admin endpoints are marked.

### Volumes

`GET /api/v1/services/{id}/volumes/` — list entries. Owner-only.

`POST /api/v1/services/{id}/volumes/write/` — write a file. Body is the file contents.

```bash
curl -sS -X POST http://localhost:8000/api/v1/services/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/volumes/write/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @local-file.txt
```

`DELETE /api/v1/services/{id}/volumes/delete/?path=…` — delete. `recursive=true` for directories.

### Cloud Storage

`GET /api/v1/cloud-storage/` — list destinations owned by the caller (or all, for admins).

`POST /api/v1/cloud-storage/` — create a destination.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string | A human-readable name. |
| `provider` | `R2` \| `S3` \| `MINIO` \| `B2` \| `DO` \| `WASABI` | Selects the endpoint template. |
| `access_key` | string | Required. Encrypted at rest. |
| `secret_key` | string | Required. Encrypted at rest. |
| `bucket` | string | Required. |
| `endpoint_url` | string | Optional. Required for MinIO. Defaults are filled in for known providers. |
| `region` | string | Optional. |
| `path_prefix` | string | Optional. |

```bash
curl -sS -X POST http://localhost:8000/api/v1/cloud-storage/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "prod-r2",
    "provider": "R2",
    "access_key": "...",
    "secret_key": "...",
    "bucket": "grid-backups",
    "endpoint_url": "https://<account>.r2.cloudflarestorage.com"
  }'
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | Endpoint URL fails scheme validation; bucket is empty. |
| 401 | Destination's `access_key` / `secret_key` cannot authenticate against the bucket (the platform performs a `HeadBucket` on create). |

### Backups

`GET /api/v1/backups/?service_id=…` — list service backups. Owner-scoped.

`POST /api/v1/backups/` — create a service backup. The body accepts `service_id` and an optional `trigger` (defaults to `MANUAL`).

```bash
curl -sS -X POST http://localhost:8000/api/v1/backups/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service_id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21"}'
```

`POST /api/v1/backups/{id}/restore/` — restore. Requires `confirm=True`.

```bash
curl -sS -X POST http://localhost:8000/api/v1/backups/3e4f5a6b-7c8d-9e0f-1a2b-3c4d5e6f7a8b/restore/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"confirm": true}'
```

`GET /api/v1/backups/{id}/download/?signed=<jwt>` — signed download.

### Server Backups

`GET /api/v1/server/backups/` — list server backups. Admin only.

`POST /api/v1/server/backups/` — create a server backup. Admin only.

`POST /api/v1/server/backups/{id}/restore/` — restore. Admin only. See [docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md#restore-procedure-for-a-full-server) for the full procedure.

### Schedules

`GET /api/v1/backup-schedules/?service_id=…` — list schedules for a service. Owner-scoped.

`POST /api/v1/backup-schedules/` — create a schedule.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `service` | UUID | `null` for a server-wide schedule. |
| `cron` | string | 5-field cron expression. |
| `retention_days` | int | Optional. |
| `retention_count` | int | Optional. |
| `destination` | UUID | Optional. `CloudStorageDestination` UUID. |
| `enabled` | bool | Default `true`. |

```bash
curl -sS -X POST http://localhost:8000/api/v1/backup-schedules/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
    "cron": "0 */6 * * *",
    "retention_count": 7,
    "destination": "..."
  }'
```

## Security

### Cross-Tenant ACL (Now Fixed)

A long-standing ACL bug in `CloudStorageViewSet` allowed a low-privilege user to read another user's destination by guessing the destination's UUID. The fix landed in Batch C: `get_object()` now checks `obj.owner == request.user` (or `request.user.is_staff`) and returns HTTP 404 (not 403) on mismatch — 404 to avoid leaking the existence of the destination. The fix is unit-tested with a cross-tenant probe.

### Encrypted Credentials

All access keys, secret keys, and SSH keys are `EncryptedCharField` / `EncryptedTextField`. The encryption is Fernet with `BACKUP_ENCRYPTION_KEY` as the master key (or `AI_ENCRYPTION_KEY` for AI-provider keys — see [docs/ai.md](ai.md#encrypted-api-keys-at-rest)). The platform never returns the decrypted value in any API response.

### Path Traversal

Volume operations normalize the requested path and check that the result is within the volume root. Any `..` segment, absolute prefix, or symlink escape returns HTTP 400 and is logged to `AuditLog` with `actor='PATH_TRAVERSAL_BLOCKED'`.

### Decryption on Restore

Decryption happens on the controller, not on the host that hosts the volume. The decrypted archive is streamed to the host over SSH, then unpacked into the volume. The decrypted scratch directory is removed after the restore (success or failure) — see [Decrypted Backup File Cleanup](#decrypted-backup-file-cleanup).

## Troubleshooting

### "Backup file not found at expected path"

The local archive is missing. Check the destination's `path_prefix` if you are restoring from a cloud destination. For a local archive, the platform's `/var/lib/grid/backups/` directory may have been pruned by an external cron — the `BACKUP_RETENTION_COUNT` retentor is the only sanctioned pruner; anything else is operator error.

### "BACKUP_ENCRYPTION_KEY mismatch on restore"

The current `.env` does not have the key that was used to encrypt the backup. Append the old key to the comma-separated list (see [Multi-Key Support](#multi-key-support)) and restart the backend.

### "Decrypted scratch directory is filling up /tmp"

The Celery beat cleanup task is not running. Restart the beat scheduler. The cleanup task is registered as `cleanup_orphaned_decrypted_backups` and runs every 15 minutes.

### "Cross-tenant ACL fix did not take effect"

Confirm the running build is from after Batch C. The `get_object()` override is in `apps/storage/views.py`. Check the build date: `git log -1 --format=%cd -- apps/storage/views.py`.

### "Cloud storage destination creation returns 401"

The `HeadBucket` probe failed. The platform tests the credentials at create time. Verify the access key / secret key, the bucket name, and the endpoint URL. For self-hosted MinIO, ensure the bucket exists and the platform's outbound network can reach it.

### "Restore hangs at 'pre-restore snapshot'"

The pre-restore snapshot is taking longer than expected. The platform's snapshot task has a 30-minute timeout; after that the restore is aborted. For very large volumes, the recommended path is to take a manual snapshot first, then restore from it.

## Limitations

- **No block-level snapshots.** Backups are tarballs of the volume's file system. Large databases with high write rates may not be consistent point-in-time (the platform uses `docker exec <db> pg_dump` for consistency, but for non-Postgres databases the snapshot is crash-consistent only).
- **No deduplication across backups.** Each backup is independent. A high-frequency schedule will produce a large archive set.
- **No incremental backups.** A backup is always a full snapshot.
- **No off-host backup verification.** The platform writes and reads the backup on the same host. A silent disk failure can corrupt both the live data and the backup.
- **Decrypted scratch is on the controller, not the target.** A multi-host restore (target != source) requires the controller to have enough disk to hold the decrypted archive.
- **The audit log is included in server backups.** A `purge_user_backups_task` redacts the audit log to remove the deleted user's actions, but the redaction is a soft delete — the row's `metadata.user_id` is set to `null` but the row itself is preserved for the audit chain's hash integrity.
- **No Windows-native paths.** Volume operations assume POSIX paths.
- **`purge_user_backups_task` is best-effort.** A cloud destination that is unreachable at deletion time leaves orphan objects. The platform retries up to 3 times with exponential back-off, then logs the failure to `AuditLog` and moves on. The operator is expected to clean up manually.
