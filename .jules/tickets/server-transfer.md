# Server Transfer (Migration Between Servers)

## Context
Users need to migrate their services from one VPS to another with minimal downtime. This depends on the Backup System being complete first (see `backup-system.md`).

## Prerequisites
- Backup System (Phase 1 from `backup-system.md`) must be fully implemented
- Both source and target servers must have SMSLY Hosting installed
- Target server must have Docker, Traefik, and the smsly-hosting stack running

## Codebase Location
- Backend: `backend/apps/deployments/`
- Cloud adapter: `backend/apps/cloud/adapters/local.py`
- Models: `backend/apps/deployments/models.py`
- Frontend: `frontend/src/`

## Phase 1: Server Transfer Models & Service

### 1.1 Create transfer models
File: `backend/apps/deployments/models_transfer.py` [NEW]

```python
class ServerTransfer(models.Model):
    """Tracks migration of services from source to target server."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    status = models.CharField(choices=[
        ('PREPARING', 'Preparing'),        # creating backup on source
        ('UPLOADING', 'Uploading'),         # transferring to target
        ('RESTORING', 'Restoring'),         # restoring on target
        ('DNS_CUTOVER', 'DNS Cutover'),     # waiting for DNS propagation
        ('VERIFYING', 'Verifying'),         # health checks on target
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('ROLLED_BACK', 'Rolled Back'),
    ], default='PREPARING', max_length=20)

    # Source
    source_server_ip = models.GenericIPAddressField()
    source_backup = models.ForeignKey('ServiceBackup', on_delete=models.SET_NULL, null=True)

    # Target
    target_server_ip = models.GenericIPAddressField()
    target_ssh_key = models.TextField(blank=True)  # encrypted SSH key for target

    # Scope
    transfer_type = models.CharField(choices=[
        ('SERVICE', 'Single Service'), ('FULL', 'Full Server'),
    ], max_length=20)
    service = models.ForeignKey('Service', on_delete=models.SET_NULL, null=True, blank=True)

    # Progress
    progress_percent = models.IntegerField(default=0)
    current_step = models.CharField(max_length=200, blank=True)
    logs = models.TextField(blank=True)
    error_message = models.TextField(blank=True)

    # Timing
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    estimated_downtime_seconds = models.IntegerField(default=0)

    # Rollback
    can_rollback = models.BooleanField(default=True)
    rollback_deadline = models.DateTimeField(null=True)  # after this, source cleaned up
```

### 1.2 Create transfer service
File: `backend/apps/deployments/services/transfer_service.py` [NEW]

Implement `ServerTransferService`:

```python
class ServerTransferService:
    def __init__(self, transfer: ServerTransfer):
        self.transfer = transfer

    def execute(self):
        """Full transfer pipeline."""
        try:
            self._prepare()      # 10% — create backup
            self._upload()       # 40% — rsync/scp backup to target
            self._restore()      # 70% — restore on target server
            self._dns_cutover()  # 85% — update DNS records
            self._verify()       # 95% — health checks on target
            self._complete()     # 100%
        except Exception as e:
            self._handle_failure(e)

    def _prepare(self):
        """Step 1: Create backup on source server."""
        # Call backup_service() from backup system
        # Store backup reference in self.transfer.source_backup
        self._update(10, 'Creating backup on source server...')

    def _upload(self):
        """Step 2: Transfer backup to target server via rsync."""
        # rsync -avz --progress /backups/{file} root@{target_ip}:/opt/smsly-hosting/backups/
        # Use paramiko or subprocess with SSH key
        # Stream progress via WebSocket
        self._update(40, 'Transferring backup to target server...')

    def _restore(self):
        """Step 3: Restore on target server."""
        # SSH to target: call restore API or run restore script
        # POST https://{target_ip}/api/v1/server/backups/{id}/restore/
        self._update(70, 'Restoring services on target server...')

    def _dns_cutover(self):
        """Step 4: Update DNS A records to point to target IP."""
        # For each service with a custom domain:
        #   - Log which domains need DNS update
        #   - If CloudFlare API configured: auto-update A records
        #   - Otherwise: show user the DNS changes needed
        self._update(85, 'DNS cutover — update A records to new server IP...')

    def _verify(self):
        """Step 5: Health check all services on target."""
        # For each service:
        #   - curl https://{domain} — expect 200
        #   - Check container status on target
        #   - Run AI diagnosis if any fail
        self._update(95, 'Verifying services on target server...')

    def _complete(self):
        """Step 6: Mark complete, keep source as rollback for 48h."""
        self.transfer.status = 'COMPLETED'
        self.transfer.rollback_deadline = timezone.now() + timedelta(hours=48)
        self.transfer.save()
        self._update(100, 'Transfer complete! Source preserved for 48h rollback.')

    def rollback(self):
        """Revert: point DNS back to source, stop target containers."""
        # Only allowed before rollback_deadline
        # 1. Update DNS back to source IP
        # 2. Stop containers on target
        # 3. Mark transfer as ROLLED_BACK

    def _update(self, percent, step):
        self.transfer.progress_percent = percent
        self.transfer.current_step = step
        self.transfer.save(update_fields=['progress_percent', 'current_step'])
        broadcast_status(self.transfer)  # real-time UI updates
```

### 1.3 Create Celery tasks
File: `backend/apps/deployments/tasks.py` — add:

```python
@shared_task(bind=True, soft_time_limit=7200, time_limit=7500)
def execute_server_transfer_task(self, transfer_id):
    transfer = ServerTransfer.objects.get(id=transfer_id)
    ServerTransferService(transfer).execute()

@shared_task(bind=True)
def rollback_transfer_task(self, transfer_id):
    transfer = ServerTransfer.objects.get(id=transfer_id)
    ServerTransferService(transfer).rollback()
```

## Phase 2: API Endpoints

File: `backend/apps/deployments/views.py` — add:

```
POST   /api/v1/transfers/                     → initiate transfer
GET    /api/v1/transfers/                     → list transfers
GET    /api/v1/transfers/{id}/                → transfer status + progress
POST   /api/v1/transfers/{id}/rollback/       → rollback to source
DELETE /api/v1/transfers/{id}/                → cancel in-progress transfer
```

File: `backend/apps/deployments/serializers.py` — add:
- `ServerTransferSerializer` (with progress, status, logs)
- `ServerTransferCreateSerializer` (target_ip, ssh_key, transfer_type, service_id)

## Phase 3: Frontend UI

### 3.1 Server Transfer page
File: `frontend/src/app/transfers/page.tsx` [NEW]

UI components:
- **Transfer wizard** (3 steps):
  1. Select scope: single service or full server
  2. Enter target server IP + SSH key
  3. Review + confirm (shows estimated downtime)
- **Progress view**: real-time progress bar with step labels, streaming logs
- **Transfer history**: table of past transfers with status
- **Rollback button**: visible for 48h after completion

### 3.2 Wire into navigation
File: `frontend/src/components/layout/Sidebar.tsx` — add under "Servers":
- "Transfer" sub-item

### 3.3 Per-service transfer
File: `frontend/src/components/settings/` — add "Transfer" button in service settings
- Prefills the transfer wizard with the selected service

## Phase 4: DNS Automation (Optional Enhancement)

### 4.1 CloudFlare integration
File: `backend/apps/cloud/services/dns_service.py` [NEW]

```python
class DNSService:
    def __init__(self, provider='cloudflare'):
        self.api_key = os.environ.get('CLOUDFLARE_API_KEY')

    def update_a_record(self, domain, new_ip):
        """Update A record via CloudFlare API."""
        ...

    def get_current_ip(self, domain):
        """Lookup current A record."""
        ...
```

### 4.2 Add DNS provider config
File: `backend/apps/cloud/models.py` — add `DNSProvider` model or env vars:
- `CLOUDFLARE_API_KEY`
- `CLOUDFLARE_ZONE_ID`

## Validation
1. Transfer single service → verify it runs on target with same domain
2. Transfer full server → verify all services + addons + SSL certs restored
3. Rollback within 48h → verify source resumes correctly
4. Transfer with custom domain → verify DNS cutover works
5. Transfer large service (>5GB volumes) → verify rsync handles it
6. Cancel mid-transfer → verify cleanup runs on both servers
7. Transfer to server with existing services → verify no conflicts

## Anti-Crash Rules
- Never delete source data until rollback deadline (48h)
- Use `rsync --checksum` to verify transfer integrity
- Encrypt backup in transit (rsync over SSH)
- Log every step with timestamps for debugging
- If any health check fails on target → auto-rollback
- Use `soft_time_limit` on all tasks (transfers can take hours for large services)
- Stream progress via WebSocket using `broadcast_status()` for real-time UI
- Require SSH key confirmation before starting (never store plaintext)

## Dependencies
- `paramiko` — SSH/SFTP for remote operations
- `rsync` — efficient file transfer (installed on both servers)
- Backup System — must be complete before starting this ticket
