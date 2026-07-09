import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shlex
import socket
import tempfile
import time
from datetime import timedelta

import requests
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from ..models import PlatformConfig  # type: ignore[attr-defined]
from .backup_service import BackupService, UnknownBackupKeyIdError

logger = logging.getLogger(__name__)

TRANSFER_LOG_LIMIT = getattr(settings, "TRANSFER_LOG_LIMIT", 100 * 1024)
TRANSFER_ERROR_LIMIT = 4_000


def get_transfer_log_limit():
    return getattr(settings, "TRANSFER_LOG_LIMIT", TRANSFER_LOG_LIMIT)

# SECURITY (Batch G): the .env keys that MUST NOT be shipped to the
# target during a FULL server transfer. These are platform-level
# secrets whose loss compromises the source platform. The
# operator must re-enter them on the target after the transfer.
#
# NOTE: FIELD_ENCRYPTION_KEY is intentionally NOT in this set. The
# source's .env ships the same FIELD_ENCRYPTION_KEY so the target
# can decrypt the encrypted database rows shipped in db_dump.sql.
# Without this, every EncryptedCharField row (service env vars,
# ManagedServer SSH credentials, ServerTransfer SSH passwords,
# BackupEncryptionKey key material, etc.) would silently decrypt
# to "" on the target because EncryptedCharField is configured to
# swallow InvalidToken and return "" (see Batch J safe_to_python).
_TRANSFER_SCRUB_KEYS = frozenset({
    "BACKUP_ENCRYPTION_KEY",
    "GATEWAY_SECRET",
    "CLOUDFLARE_API_TOKEN",
    "SENTRY_DSN",
    "WEBHOOK_SECRET",
    "OAUTH_CLIENT_SECRET",
    "INTERNAL_API_TOKEN",
    "JWT_SIGNING_KEY",
    "GITLAB_SECRET_TOKEN",
    "GITHUB_WEBHOOK_SECRET",
    "BITBUCKET_WEBHOOK_SECRET",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "SMTP_PASSWORD",
    "DATABASE_URL",  # contains password in URL form
    "REDIS_URL",     # contains password in URL form
})


def _scrub_env_for_transfer(path: str) -> str:
    """Read a .env file and return a scrubbed copy with platform
    secrets stripped. Comments are preserved; quoted values are
    preserved; the format of the file is unchanged so the target's
    installer / process manager can read it.

    The output is a string, not bytes, so callers should write it
    in text mode. Lines that are empty or pure comments are kept
    verbatim; key/value pairs whose key is in ``_TRANSFER_SCRUB_KEYS``
    are replaced with a comment that flags the key as
    "operator-must-set" so the install UI can prompt the user.
    """
    scrubbed_lines = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            stripped = line.lstrip()
            # Preserve comments and empty lines verbatim
            if not stripped or stripped.startswith("#"):
                scrubbed_lines.append(line)
                continue
            # Find "=" not inside quotes (we don't try to be perfect
            # — this is a .env file with simple KEY=VALUE pairs).
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", stripped)
            if not m:
                scrubbed_lines.append(line)
                continue
            key, _ = m.group(1), m.group(2)
            if key in _TRANSFER_SCRUB_KEYS:
                scrubbed_lines.append(
                    f"# {key}=<OPERATOR-MUST-SET-AFTER-TRANSFER>  # scrubbed by Batch G"
                )
            else:
                scrubbed_lines.append(line)
    return "\n".join(scrubbed_lines) + "\n"


def _command_text(result) -> str:
    """Normalize SSHClient output while tolerating older string-returning mocks."""
    if isinstance(result, tuple):
        stdout = result[0] if len(result) > 0 else ""
        stderr = result[1] if len(result) > 1 else ""
        return (stdout or "") + (("\n" + stderr) if stderr else "")
    return "" if result is None else str(result)


def _safe_service_name(name: str) -> str:
    """Sanitize service name to alphanumeric and basic safe chars only."""
    return re.sub(r'[^a-zA-Z0-9 _.-]', '', name)[:255]


def _safe_backup_basename(file_path: str) -> str:
    """Extract a safe filename from a backup path, preventing path traversal."""
    name = os.path.basename(file_path)
    name = re.sub(r'[^a-zA-Z0-9_.-]', '', name)
    return name[:255]


_PATTERNS = [
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        flags=re.DOTALL,
    ),
    re.compile(
        r"(?i)((?:TOKEN|SECRET|PASSWORD|KEY|DSN|DATABASE_URL|REDIS_URL|AMQP_URL|BROKER_URL|API_KEY)[A-Z0-9_]*=)([^\s\"']+)",
    ),
    re.compile(
        r"Bearer\s+[A-Za-z0-9._-]+",
    ),
    re.compile(
        r"postgres(ql)?://[^\s]+:[^\s]+@",
    ),
    re.compile(
        r"(?i)((?:Authorization|X-API-Key|X-Auth-Token):\s*)(\S+)",
    ),
    re.compile(
        r"(?:https?://)[^:/\s]+:[^@\s]+@",
    ),
]


def _redact_transfer_text(text: str) -> str:
    """Keep persisted transfer logs useful without storing secrets."""
    if not text:
        return ""
    safe = str(text).replace("\x00", "")
    for idx, pat in enumerate(_PATTERNS):
        if idx == 0:
            safe = pat.sub(
                "-----BEGIN PRIVATE KEY-----***-----END PRIVATE KEY-----",
                safe,
            )
        elif idx == 1:
            safe = pat.sub(r"\1***", safe)
        elif idx in (2, 3):
            safe = pat.sub("***", safe)
        elif idx == 4:
            safe = pat.sub(r"\1***", safe)
        else:
            safe = pat.sub("***@", safe)
    return safe


class ServerTransferService:
    def __init__(self, transfer):
        self.transfer = transfer
        self.ssh = None
        self._uploaded_remote_backup_path = None
        self._target_transfer_id = None

    def _log(self, message):
        """Append a timestamped message to the transfer logs."""
        ts = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        line = _redact_transfer_text(f"[{ts}] {message}\n")
        combined = (self.transfer.logs or "") + line
        cap = get_transfer_log_limit()
        if len(combined) > cap:
            combined = (
                "--- Older transfer log output truncated to keep this record bounded ---\n"
                + combined[-cap:]
            )
        self.transfer.logs = combined
        self.transfer.save(update_fields=['logs'])
        logger.info("Transfer %s: %s", self.transfer.id, _redact_transfer_text(message))

    def _target_is_local(self):
        """Return True when the transfer target is the local machine."""
        ip = (self.transfer.target_server_ip or '').strip()
        if not ip:
            return True
        local_ips = {'127.0.0.1', 'localhost', ''}
        try:
            cfg = PlatformConfig.load()
            if cfg and cfg.server_ip:
                local_ips.add(cfg.server_ip.strip())
        except Exception:
            pass
        return ip in local_ips or ip.startswith('10.100.0.')

    def _node_api_url(self):
        """Return the base URL for the target node's API."""
        ip = self.transfer.target_server_ip or '127.0.0.1'
        return f"http://{ip}"

    def _candidate_node_urls(self) -> list[str]:
        """Return candidate base URLs for the target node, trying multiple
        transports in priority order (mirrors RemoteOrchestrator pattern).

        Priority:
          1. WireGuard mesh VPN IP (internal, fast)
          2. Public IP / domain (fallback)
        """
        urls: list[str] = []
        target_ip = (self.transfer.target_server_ip or '').strip()
        if not target_ip:
            return urls

        server = self._target_server_record()
        wg_ip = str(getattr(server, 'wg_address', '') or '').strip() if server else ''
        getattr(server, 'is_lite_agent', False) if server else False
        host = str(getattr(server, 'host', '') or '').strip() if server else ''

        def add(url: str):
            url = url.rstrip('/')
            if url and url not in urls:
                urls.append(url)

        # Priority 1: WireGuard mesh VPN (internal, fast)
        if wg_ip and wg_ip != target_ip:
            add(f"http://{wg_ip}")
            add(f"http://{wg_ip}:8090")

        # Priority 2: Target IP directly
        add(f"http://{target_ip}")
        add(f"http://{target_ip}:8090")

        # Priority 3: Public host (if different from target_ip)
        if host and host != target_ip:
            add(f"http://{host}")
            add(f"http://{host}:8090")

        return urls

    def _node_api_request(self, action, method='POST', body=None, params=None, timeout=120):
        """Call an incoming REST endpoint on the target node.

        Replaces SSH-based operations. Uses HMAC V2 auth signed with the
        TARGET's gateway_secret (matching what the target's middleware verifies).
        Adds X-SMSLY-Remote-Sync: 1 to bypass middleware HMAC and trigger
        RemoteSyncHMACAuthentication.
        Tries multiple candidate URLs with a fast TCP pre-filter.
        """
        target_ip = self.transfer.target_server_ip
        if not target_ip:
            raise RuntimeError("target_server_ip not set on transfer")

        # Use the target transfer ID if captured from register_incoming,
        # otherwise fall back to the source transfer ID.
        transfer_id = self._target_transfer_id or str(self.transfer.id)
        path = f"/api/v1/transfers/{transfer_id}/{action}/"

        # Build the exact path the server will see (with query params)
        # so HMAC signatures match on both sides.
        sig_path = path
        if params:
            from urllib.parse import urlencode
            qs = urlencode(params)
            sig_path = f"{path}?{qs}"

        body_bytes = json.dumps(body).encode() if body else b''

        # Sign with the TARGET's gateway_secret (not our own).
        server = self._target_server_record()
        secret = str(getattr(server, 'gateway_secret', '') or '').strip() if server else ''
        if not secret:
            secret = str(getattr(settings, 'GATEWAY_SECRET', '') or getattr(settings, 'SECRET_KEY', '')).strip()

        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        body_hash = hashlib.sha256(body_bytes).hexdigest()
        raw_sig = f"{method}|{sig_path}|{timestamp}|{nonce}|{body_hash}"
        signature = hmac.new(secret.encode(), raw_sig.encode(), hashlib.sha256).hexdigest()

        headers = {
            'X-Gateway-Signature-V2': signature,
            'X-Request-Timestamp': timestamp,
            'X-Request-Nonce': nonce,
            'Content-Type': 'application/json',
            'X-SMSLY-Remote-Sync': '1',
        }

        candidate_urls = self._candidate_node_urls()
        if not candidate_urls:
            candidate_urls = [f"http://{target_ip}"]

        # Fast TCP pre-filter to skip dead ports
        candidate_urls = self._filter_reachable(candidate_urls)

        last_error = None
        self._log(f"REST {method} {path} (trying {len(candidate_urls)} URL(s))")
        for base_url in candidate_urls:
            url = f"{base_url.rstrip('/')}{path}"
            try:
                resp = requests.request(method, url, headers=headers, json=body, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                last_error = e
                self._log(f"  -> {base_url} failed: {e}")
                continue

        raise RuntimeError(f"Node API call to {path} failed on all candidate URLs: {last_error}")

    @staticmethod
    def _filter_reachable(urls: list[str], probe_timeout: float = 1.0) -> list[str]:
        """Pre-filter candidate URLs by fast TCP connect probe. Skip dead endpoints."""
        import socket as sock_module
        reachable: list[str] = []
        for url in urls:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname or ""
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if not host:
                reachable.append(url)
                continue
            try:
                with sock_module.create_connection((host, port), timeout=probe_timeout):
                    reachable.append(url)
            except (TimeoutError, OSError):
                pass
        if not reachable and urls:
            reachable = urls
        return reachable

    def _local_exec(self, command, timeout=60, raise_on_error=True):
        """Execute a command locally when the target is the local machine."""
        import subprocess as sp
        self._log(f"[local] {command[:200]}")
        try:
            parts = shlex.split(command) if isinstance(command, str) else command
            proc = sp.run(
                parts, shell=False, capture_output=True, text=True,
                timeout=timeout,
            )
            if proc.returncode != 0 and raise_on_error:
                raise RuntimeError(
                    f"Local command failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
                )
            return proc.stdout, proc.stderr, proc.returncode
        except sp.TimeoutExpired:
            if raise_on_error:
                raise RuntimeError(f"Local command timed out after {timeout}s")
            return '', f'timeout after {timeout}s', -1

    def _target_server_record(self):
        from ..models_core import ManagedServer

        target = str(self.transfer.target_server_ip or '').strip()
        if not target:
            return None
        return ManagedServer.objects.filter(Q(host=target) | Q(private_ip=target)).first()

    def _build_sync_auth_headers(self, body: bytes, path: str) -> dict:
        headers = {'Content-Type': 'application/json'}
        server = self._target_server_record()

        token = str(getattr(server, 'api_token', '') or '').strip() if server else ''
        if token:
            if token.lower().startswith(('bearer ', 'token ')):
                headers['Authorization'] = token
            elif token.startswith('smsly_'):
                headers['Authorization'] = f'Bearer {token}'
            else:
                headers['Authorization'] = f'Token {token}'

        secret = str(getattr(server, 'gateway_secret', '') or '').strip() if server else ''
        if not secret:
            secret = str(
                getattr(settings, 'GATEWAY_SECRET', '') or getattr(settings, 'SECRET_KEY', '')
            ).strip()

        if secret:
            timestamp = str(int(time.time()))
            nonce = secrets.token_urlsafe(16)
            body_hash = hashlib.sha256(body).hexdigest()
            # SECURITY (Batch G): bind the nonce into the signed
            # payload so a captured request cannot be replayed with
            # a fresh nonce. Matches the format expected by
            # views_transfer._verify_transfer_sync_hmac.
            payload = f"POST|{path}|{timestamp}|{nonce}|{body_hash}"
            signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            headers['X-Request-Timestamp'] = timestamp
            headers['X-Request-Nonce'] = nonce
            headers['X-Gateway-Signature-V2'] = signature

        return headers

    def _sync_target_dashboard(self):
        """Notify the target server of this incoming transfer for dashboard visibility."""
        try:
            path = "/api/v1/transfers/register-incoming/"
            server = self._target_server_record()
            if not server:
                self._log(f"Warning: No ManagedServer record found for {self.transfer.target_server_ip}. Sync skipped.")
                return

            from .remote_orchestrator import RemoteOrchestrator
            orch = RemoteOrchestrator(server)

            payload = {
                'source_ip': self.transfer.source_server_ip,
                'target_ip': self.transfer.target_server_ip,
                'transfer_type': self.transfer.transfer_type,
                'service_name': self.transfer.service.name if self.transfer.service else None
            }

            resp = orch._request("POST", path, payload=payload, timeout=10)

            if resp and resp.status_code in (200, 201):
                try:
                    data = resp.json()
                    target_id = data.get('id')
                    if target_id:
                        self._target_transfer_id = target_id
                        self._log(f"Target dashboard synchronized (target transfer ID: {target_id}).")
                    else:
                        self._log("Target dashboard synchronized (no ID returned).")
                except Exception:
                    self._log("Target dashboard synchronized.")
            else:
                code = resp.status_code if resp else "timeout"
                self._log(f"Warning: Could not sync target dashboard (HTTP {code}).")
        except Exception as e:
            self._log(f"Warning: Target dashboard sync skipped: {e}")

    def execute(self):
        """Run transfer pipeline using REST API calls to the target node.

        All operations use the node's Django REST API with HMAC V2
        authentication — no SSH credentials needed.
        """
        try:
            self.transfer.status = 'PREPARING'
            self.transfer.save(update_fields=['status'])
            self._sync_target_dashboard()
            self._prepare()

            self.transfer.status = 'UPLOADING'
            self.transfer.save(update_fields=['status'])
            self._upload()

            self.transfer.status = 'RESTORING'
            self.transfer.save(update_fields=['status'])
            self._restore()

            self.transfer.status = 'DNS_CUTOVER'
            self.transfer.save(update_fields=['status'])
            self._stop_source_service()
            self._dns_cutover()

            self.transfer.status = 'VERIFYING'
            self.transfer.save(update_fields=['status'])
            self._verify()

            self._complete()
        except Exception as exc:
            self._handle_failure(exc)

    def _prepare(self):
        """Step 1: create source backup and verify target via REST API."""
        self._update(5, 'Pre-flight: checking target server...')

        if not self._target_is_local():
            result = self._node_api_request('incoming/ensure-docker')
            if not result.get('docker_available'):
                self._log("Docker not detected on target — will be installed by the platform.")
            # Target backend API is available (we just called it) — that's sufficient.

        self._update(10, 'Creating backup on source server...')

        backup_svc = BackupService()
        if self.transfer.transfer_type == 'SERVICE':
            if not self.transfer.service:
                raise ValueError("Service ID required for SERVICE transfer.")
            backup = backup_svc.backup_service(
                self.transfer.service.id,
                backup_type='TRANSFER',
            )
            self.transfer.source_backup = backup
            self.transfer.save(update_fields=['source_backup'])
        else:
            backup = backup_svc.backup_server()
            self.transfer.source_server_backup = backup
            self.transfer.save(update_fields=['source_server_backup'])

    def _upload(self):
        """Step 2: prepare backup reference for target."""
        self._update(40, 'Preparing backup for restore...')

        backup = self.transfer.source_backup or self.transfer.source_server_backup
        if not backup or not backup.file_path:
            raise ValueError("Backup file not found.")

        local_path = backup.file_path
        if local_path.endswith(".enc"):
            key = BackupService._get_encryption_key()
            if not key:
                raise ValueError("Encrypted backup detected but no backup encryption key is configured.")
            try:
                local_path = BackupService.decrypt_backup(local_path, key)
            except UnknownBackupKeyIdError as exc:
                raise ValueError(
                    f"Backup encrypted with unknown key_id={exc.key_id} "
                    f"(fingerprint={exc.fingerprint}). "
                    "Call POST /api/v1/backups/import-key/ on the target with the "
                    "source's key_id and BACKUP_ENCRYPTION_KEY to register the "
                    "foreign key, then retry the transfer."
                ) from exc

        remote_path = f"/tmp/{_safe_backup_basename(local_path)}"
        self._uploaded_remote_backup_path = local_path if self._target_is_local() else remote_path

        self._log(f"Backup prepared at {local_path} (node will pull via restore script)")

    def _export_backup_key(self) -> str | None:
        """Build a JSON bundle describing the source's BACKUP_ENCRYPTION_KEY.

        The bundle is consumed by the import script that runs in the
        target's backend container during FULL transfer restore. It
        lets the target register the source's Fernet key by its
        ``key_id`` so any backup created on the source remains
        readable on the target (cross-master restore).

        Returns the path to a temp file containing the JSON, or
        ``None`` if no BACKUP_ENCRYPTION_KEY is configured (nothing
        to migrate). Returns ``None`` for SERVICE transfers — they
        don't need cross-master key registration.
        """
        if self.transfer.transfer_type != 'FULL':
            return None
        key_material = BackupService._get_encryption_key()
        if not key_material:
            return None
        try:
            fingerprint = BackupService.compute_backup_key_fingerprint(key_material)
        except Exception as exc:
            self._log(f"Could not compute backup key fingerprint: {exc}")
            return None
        try:
            from apps.deployments.models_backup import BackupEncryptionKey
            row = (
                BackupEncryptionKey.objects
                .filter(is_active=True, fingerprint=fingerprint)
                .first()
            )
        except Exception as exc:
            self._log(f"Could not look up active BackupEncryptionKey: {exc}")
            row = None
        if row is None:
            self._log(
                "No active BackupEncryptionKey row found for source's "
                f"BACKUP_ENCRYPTION_KEY (fingerprint={fingerprint}). "
                "The target will generate a new key on first use; historical "
                "backups created on the source will require manual key import."
            )
            return None
        source_ip = (
            self.transfer.source_server_ip
            or getattr(self.transfer.source_server, "host", None)
            or "unknown"
        )
        bundle = {
            'key_id': row.key_id,
            'key_material': key_material,
            'fingerprint': fingerprint,
            'source_label': f'migrated-from-{source_ip}',
        }
        fd, path = tempfile.mkstemp(prefix='backup_key_export_', suffix='.json')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(bundle, f)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(path)
            raise
        return path

    def _restore(self):
        """Step 3: restore on target via REST API."""
        self._update(60, 'Restoring services on target server...')

        backup = self.transfer.source_backup or self.transfer.source_server_backup
        backup_filename = _safe_backup_basename(backup.file_path)
        remote_backup_path = (
            self._uploaded_remote_backup_path
            or f"/tmp/{backup_filename}"
        )

        if self.transfer.transfer_type == 'SERVICE':
            self._restore_single_service(remote_backup_path)
        else:
            self._restore_full_server_rest(remote_backup_path)

    def _restore_single_service(self, remote_backup_path):
        """Restore a single service on target via REST API (no SSH)."""
        target_server = self._target_server_record()
        is_lite_agent = getattr(target_server, 'is_lite_agent', False) if target_server else False

        # For lite agents, the service row already exists in shared DB.
        # Just load the image and start the container.
        if is_lite_agent:
            self._restore_single_service_lite(remote_backup_path)
            return

        # For full nodes, send the restore trigger script via REST exec.
        self._update(75, 'Hydrating Service on target via REST API...')
        owner_email = self.transfer.service.owner.email if self.transfer.service and self.transfer.service.owner else None
        restore_script = self._build_restore_trigger_script(owner_email, remote_backup_path)
        exec_result = self._node_api_request('incoming/exec', body={
            'script': restore_script,
            'container': 'backend',
        })
        if exec_result.get('exit_code', 0) != 0:
            raise RuntimeError(f"Remote service hydration failed: {exec_result.get('stdout', '')[:500]}")

        # Pull the image on the target
        metadata = self.transfer.source_backup.metadata
        image = metadata.get('docker_image') or self.transfer.service.docker_image
        if image:
            self._update(85, 'Pulling service image on target...')
            self._node_api_request('incoming/pull-image', body={'image': image})

        # Remap env vars for the target platform
        self._remap_target_platform_env()

        # Generate and send deploy command via REST
        self._update(90, 'Starting service container on target...')
        env_vars = metadata.get('env_vars', [])
        env_dict = {e['key']: e['value'] for e in env_vars}
        name = self.transfer.service.name
        port = self.transfer.service.internal_port
        domain = self.transfer.service.public_domain
        labels = {
            'traefik.enable': 'true',
            'traefik.docker.network': 'smsly-net',
            f'traefik.http.routers.{name}.rule': f'Host(`{domain}`)',
            f'traefik.http.routers.{name}.service': name,
            f'traefik.http.services.{name}.loadbalancer.server.port': str(port),
            'managed_by': 'smsly-hosting',
        }

        self._node_api_request('incoming/deploy', body={
            'image': image,
            'container_name': name,
            'env': env_dict,
            'labels': labels,
            'network': 'smsly-net',
        })
        self._seed_target_deployment_record(metadata)

    def _restore_single_service_lite(self, remote_backup_path):
        """Restore a single service on a Lite Agent target via REST."""
        self._update(65, 'Restoring service on lite agent target...')

        metadata = self.transfer.source_backup.metadata
        image = metadata.get('docker_image') or self.transfer.service.docker_image

        # Pull the image on the lite agent
        if image:
            self._update(75, 'Pulling service image on lite agent...')
            self._node_api_request('incoming/pull-image', body={'image': image})

        # Start service container via REST
        self._update(90, 'Starting service container on lite agent...')
        env_vars = metadata.get('env_vars', [])
        env_dict = {e['key']: e['value'] for e in env_vars}
        name = self.transfer.service.name
        port = self.transfer.service.internal_port
        domain = self.transfer.service.public_domain
        labels = {
            'traefik.enable': 'true',
            'traefik.docker.network': 'smsly-net',
            f'traefik.http.routers.{name}.rule': f'Host(`{domain}`)',
            f'traefik.http.routers.{name}.service': name,
            f'traefik.http.services.{name}.loadbalancer.server.port': str(port),
            'managed_by': 'smsly-hosting',
        }

        self._node_api_request('incoming/deploy', body={
            'image': image,
            'container_name': name,
            'env': env_dict,
            'labels': labels,
            'network': 'smsly-net',
        })

    def _exec_on_target(self, script, container='backend', timeout=120):
        """Execute a Python script on the target node via REST API."""
        return self._node_api_request('incoming/exec', body={
            'script': script,
            'container': container,
        }, timeout=timeout)

    def _remap_target_platform_env(self, backend_container=None):
        """
        Replace source-local platform URLs that cannot resolve on the target.
        Uses REST API instead of SSH.
        """
        if not self.transfer.service:
            return

        service_name = _safe_service_name(self.transfer.service.name)
        payload = {'service_name': service_name}
        remap_code = """
import json
import os
import socket
from urllib.parse import urlparse
from apps.deployments.models import Service, EnvironmentVariable

payload = json.loads(%r)
svc = Service.objects.filter(name=payload["service_name"]).first()
platform_database_url = os.environ.get("DATABASE_URL", "").strip()
platform_redis_url = os.environ.get("REDIS_URL", "").strip()
pre_transfer = {}
if svc:
    url_remaps = {
        "DATABASE_URL": platform_database_url,
        "MARKETER_DATABASE_URL": platform_database_url,
        "REDIS_URL": platform_redis_url,
        "RATE_LIMIT_REDIS_URL": platform_redis_url,
        "CACHE_URL": platform_redis_url,
        "CELERY_BROKER_URL": platform_redis_url,
        "CELERY_RESULT_BACKEND": platform_redis_url,
    }
    # ── Domain/env remaps for cross-platform migration ─────────────────
    target_domain = os.environ.get("DOMAIN", "").strip()
    domain_remaps = {}
    if target_domain:
        domain_remaps = {
            "PUBLIC_DOMAIN": target_domain,
            "ALLOWED_HOSTS": f"{target_domain},localhost,127.0.0.1",
            "DJANGO_ALLOWED_HOSTS": target_domain,
            "SITE_URL": f"https://{target_domain}",
        }

    # Snapshot pre-transfer values for every candidate key so rollback can
    # restore them on the SOURCE side of the move.
    for candidate_key in list(url_remaps.keys()) + list(domain_remaps.keys()):
        env = EnvironmentVariable.objects.filter(service=svc, key=candidate_key).first()
        if env is not None:
            pre_transfer[candidate_key] = str(env.value or "")

    for dk, dv in domain_remaps.items():
        # Only update if the current value references the old platform
        env = EnvironmentVariable.objects.filter(service=svc, key=dk).first()
        if env and env.value and str(env.value).strip():
            old_val = str(env.value).strip()
            old_base = os.environ.get("DOMAIN_OLD", "").strip() or "localhost"
            if old_base in old_val or old_val == "********":
                EnvironmentVariable.objects.update_or_create(
                    service=svc, key=dk,
                    defaults={"value": dv, "source": "SYSTEM"},
                )

    for key, replacement_url in url_remaps.items():
        if not replacement_url:
            continue
        env = EnvironmentVariable.objects.filter(service=svc, key=key).first()
        value = str(env.value or "").strip() if env else ""
        parsed = urlparse(value)
        host = parsed.hostname
        should_remap = value == "********"
        if host and host not in {"redis", "localhost", "127.0.0.1"}:
            try:
                socket.getaddrinfo(host, parsed.port or 6379)
            except OSError:
                should_remap = True
        if should_remap:
            EnvironmentVariable.objects.update_or_create(
                service=svc,
                key=key,
                defaults={"value": replacement_url, "is_secret": True, "source": "SYSTEM"},
            )

# Emit the snapshot on a single line inside sentinels so the parent can
# extract it deterministically even if Django logs interleave with stdout.
print("PRE_TRANSFER_ENV_JSON_BEGIN")
print(json.dumps(pre_transfer))
print("PRE_TRANSFER_ENV_JSON_END")
""".strip() % json.dumps(payload)

        pre_transfer: dict = {}
        try:
            exec_result = self._exec_on_target(remap_code)
            output = exec_result.get('stdout', '')
            match = re.search(
                r"PRE_TRANSFER_ENV_JSON_BEGIN\s*(\{.*?\})\s*PRE_TRANSFER_ENV_JSON_END",
                output,
                re.DOTALL,
            )
            if match:
                try:
                    pre_transfer = json.loads(match.group(1)) or {}
                except json.JSONDecodeError as exc:
                    logger.warning("Could not parse pre-transfer env snapshot: %s", exc)
        except Exception as exc:
            logger.warning("Failed to remap target platform env vars: %s", exc)

        if pre_transfer:
            metadata = dict(self.transfer.metadata or {})
            metadata['pre_transfer_env_vars'] = pre_transfer
            self.transfer.metadata = metadata
            self.transfer.save(update_fields=['metadata'])

    def _revert_target_platform_env(self):
        """Restore the source platform env-var values captured at remap time.

        Reads ``self.transfer.metadata['pre_transfer_env_vars']`` (populated
        by ``_remap_target_platform_env``) and writes each key back to the
        target's EnvironmentVariable table via a small Python script run
        inside the target's backend container.

        No-op for FULL transfers, when no snapshot is present, or when the
        SSH / backend container is unavailable.
        """
        if self.transfer.transfer_type != 'SERVICE' or not self.transfer.service:
            return
        pre_transfer = (self.transfer.metadata or {}).get('pre_transfer_env_vars') or {}
        if not pre_transfer:
            return

        try:
            backend_container = self._find_remote_backend_container(required=False)
        except Exception as exc:
            self._log(f"Could not locate backend container for env revert: {exc}")
            return
        if not backend_container:
            self._log("Backend container not found on target — skipping env revert.")
            return

        service_name = _safe_service_name(self.transfer.service.name)
        shlex.quote(backend_container)
        script_path = f"/tmp/transfer_revert_env_{self.transfer.id}.py"
        shlex.quote(script_path)

        payload = {
            'service_name': service_name,
            'pre_transfer': pre_transfer,
        }
        revert_code = f"""
import json
import os
import sys
import django

for candidate in (os.getcwd(), '/app', '/app/backend'):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable

payload = json.loads({json.dumps(payload)})
svc = Service.objects.filter(name=payload['service_name']).first()
if svc:
    for key, value in payload['pre_transfer'].items():
        EnvironmentVariable.objects.update_or_create(
            service=svc,
            key=key,
            defaults={{
                'value': value,
                'is_secret': True,
                'source': 'SYSTEM',
            }},
        )
    print(f"REVERTED {{len(payload['pre_transfer'])}} env vars for {{payload['service_name']}}")
else:
    print('ERROR: service not found', file=sys.stderr)
"""
        try:
            exec_result = self._exec_on_target(revert_code)
            output = exec_result.get('stdout', '')
            if "REVERTED" in output:
                self._log(f"Reverted target platform env vars: {output.strip()}")
            else:
                self._log(f"Target env revert did not confirm: {output.strip()[:300]}")
        except Exception as exc:
            logger.warning("Failed to revert target platform env vars: %s", exc)

    def _seed_target_deployment_record(self, backend_container=None, metadata=None):
        """Create a deployment row via REST API (no SSH)."""
        if not self.transfer.service:
            return

        service_name = _safe_service_name(self.transfer.service.name)
        metadata = metadata or (self.transfer.source_backup.metadata if self.transfer.source_backup else {}) or {}
        image_ref = (
            str(metadata.get('docker_image') or '').strip()
            or str(self.transfer.service.docker_image or '').strip()
            or 'backup-restore'
        )

        payload = {
            'service_name': service_name,
            'image_ref': image_ref,
            'source_node': str(self.transfer.source_server_ip or ''),
        }
        restore_code = """
import json
from django.utils import timezone
from apps.deployments.models import Service, Deployment

payload = json.loads(%r)
service_name = payload["service_name"]
svc = Service.objects.filter(name=service_name).first()
if svc:
    latest = Deployment.objects.filter(service=svc).order_by("-created_at").first()
    if not latest:
        now = timezone.now()
        container_id = payload.get("container_id") or None
        status = Deployment.Status.ACTIVE if container_id else Deployment.Status.FAILED
        Deployment.objects.create(
            service=svc,
            status=status,
            commit_hash=(payload.get("image_ref") or "backup-restore")[-40:],
            commit_message="Seeded from interserver backup restore on target server",
            build_logs=(
                "Seeded after backup restore. "
                f"Container: {container_id or 'missing'} "
                f"Image: {payload.get('image_ref') or 'unknown'}"
            ),
            container_id=container_id,
            started_at=now,
            finished_at=now,
            source_node=payload.get("source_node") or "",
            pipeline_stages=[
                {"name": "Backup restore", "status": "done", "duration": 0},
                {
                    "name": "Target container verification",
                    "status": "done" if container_id else "failed",
                    "duration": 0,
                },
            ],
        )
""".strip() % json.dumps(payload)

        try:
            self._exec_on_target(restore_code)
        except Exception as exc:
            logger.warning("Failed to seed target deployment record: %s", exc)

    def _load_service_image_on_target(self, remote_backup_path):
        """Load the service image archive on the target Docker host.

        The remote Django restore hydrates database and volume state from inside
        the platform container. Loading the image from the host as well makes
        the final container start independent of the target container's Docker
        socket proxy behavior.
        """
        self._update(88, 'Loading service image on target Docker host...')
        extract_dir = f"/tmp/transfer_image_{self.transfer.id}"
        image_path = f"{extract_dir}/image.tar"
        metadata_path = f"{extract_dir}/metadata.json"
        read_image_ref = (
            "target_image=$(python3 -c "
            + shlex.quote(
                "import json,sys; "
                "print((json.load(open(sys.argv[1])).get('docker_image') or '').strip())"
            )
            + f" {shlex.quote(metadata_path)})"
        )
        load_image = (
            f"if [ -s {shlex.quote(image_path)} ]; then "
            f"load_output=$(docker load -i {shlex.quote(image_path)} 2>&1); "
            "printf '%s\\n' \"$load_output\"; "
            "loaded_ref=$(printf '%s\\n' \"$load_output\" | sed -n 's/^Loaded image: //p' | tail -n 1); "
            "loaded_id=$(printf '%s\\n' \"$load_output\" | sed -n 's/^Loaded image ID: //p' | tail -n 1); "
            "loaded_source=\"${loaded_ref:-$loaded_id}\"; "
            "if [ -n \"$target_image\" ] && [ -n \"$loaded_source\" ] "
            "&& ! docker image inspect \"$target_image\" >/dev/null 2>&1; then "
            "docker tag \"$loaded_source\" \"$target_image\"; "
            "fi; "
            "else echo 'No image.tar found in backup archive'; fi"
        )
        cmd = " && ".join([
            f"rm -rf {shlex.quote(extract_dir)}",
            f"mkdir -p {shlex.quote(extract_dir)}",
            f"tar -xzf {shlex.quote(remote_backup_path)} -C {shlex.quote(extract_dir)} metadata.json",
            (
                f"tar -xzf {shlex.quote(remote_backup_path)} -C {shlex.quote(extract_dir)} "
                "image.tar || true"
            ),
            read_image_ref,
            load_image,
            f"rm -rf {shlex.quote(extract_dir)}",
        ])
        self.ssh.exec_command(cmd, timeout=1200)

    def _find_remote_backend_container(self, required=False):
        """Return the best matching Grid backend container name on the target."""
        configured = getattr(
            settings, "REMOTE_BACKEND_CONTAINER_NAME", "smsly-hosting-backend-1"
        )
        candidates = []

        for cmd in (
            "docker ps --filter name=backend --format '{{.Names}}'",
            f"docker ps --filter name={shlex.quote(configured)} --format '{{{{.Names}}}}'",
        ):
            output = _command_text(
                self.ssh.exec_command(cmd, raise_on_error=False)
            ).strip()
            for raw_name in output.splitlines():
                name = raw_name.strip("'\" ")
                if name and name not in candidates:
                    candidates.append(name)

        for name in candidates:
            if 'hosting' in name and 'backend' in name:
                return name
        for name in candidates:
            if 'backend' in name:
                return name

        if required:
            raise RuntimeError(
                "Could not locate Grid backend container on target server. "
                f"Searched for: {candidates or [configured]}"
            )
        return None

    def _ensure_target_platform_started(self):
        """Start an installed Grid target when Docker is up but the stack is down.

        Handles both full-platform nodes and Lite Agents (which use
        infrastructure/docker/docker-compose.agent-lite.yml).
        """
        hosting_path = self.ssh.find_hosting_path()
        safe_path = shlex.quote(hosting_path)
        timeout = int(getattr(settings, "TRANSFER_TARGET_START_TIMEOUT", 1200))
        agent_lite = "infrastructure/docker/docker-compose.agent-lite.yml"
        cmd = " && ".join([
            f"cd {safe_path}",
            "mkdir -p caddy-config /opt/smsly-cache",
            "docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null",
            "docker network inspect smsly-proxy >/dev/null 2>&1 || docker network create smsly-proxy >/dev/null",
            "("
            f"test -f {shlex.quote(agent_lite)} "
            f"&& docker compose -f {shlex.quote(agent_lite)} up -d --build"
            " || ("
            "test -f docker-compose.prod.yml "
            "&& docker compose -f docker-compose.prod.yml up -d --build"
            " || docker compose up -d --build"
            ")"
            ")",
        ])
        self._update(8, 'Starting Grid platform on target server...')
        self.ssh.exec_command(cmd, timeout=timeout)

    def _wait_for_remote_backend_ready(self, backend_container):
        """Wait until target platform health confirms backend dependencies.

        Uses docker exec inside the backend container so it works for both
        full-platform nodes and Lite Agents (which do not expose port 8000
        to the host).
        """
        safe_container = shlex.quote(backend_container)
        command = (
            f"for i in $(seq 1 60); do "
            f"docker exec {safe_container} curl -fsS -m 5 http://localhost:8000/health/live 2>/dev/null "
            f"| grep -q '\"status\": \"alive\"' "
            f"&& echo READY && exit 0; "
            f"docker exec {safe_container} curl -fsS -m 5 http://localhost:8000/health 2>/dev/null "
            f"| grep -q '\"status\": \"healthy\"' "
            f"&& echo READY && exit 0; "
            f"sleep 5; "
            f"done; echo NOT_READY; exit 1"
        )
        output = _command_text(self.ssh.exec_command(command, timeout=330))
        if "READY" not in output:
            raise RuntimeError("Target Grid backend did not become ready before restore.")

    def _target_hosting_path(self) -> str:
        """Find the remote Grid install path with a stable fallback."""
        try:
            path = self.ssh.find_hosting_path()
            if isinstance(path, str) and path.startswith("/"):
                return path.rstrip("/")
        except Exception as exc:
            logger.warning("Could not detect target Grid install path: %s", exc)
        return "/opt/smsly-hosting"

    @staticmethod
    def _build_restore_trigger_script(owner_email, backup_path='/tmp/transfer_backup.tar.gz'):
        return f"""import os
import sys
import time
import django
import logging
from urllib.parse import quote_plus

for candidate in (os.getcwd(), '/app', '/app/backend'):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

def configure_direct_database_url():
    current_url = os.environ.get('DATABASE_URL', '')
    # Skip override on agent/lite nodes — their DATABASE_URL already
    # points directly to the controller (no pgcat pooler to bypass).
    if current_url and 'pgcat' not in current_url:
        return
    user = os.environ.get('POSTGRES_USER')
    password = os.environ.get('POSTGRES_PASSWORD')
    db_name = os.environ.get('POSTGRES_DB')
    if user and password and db_name:
        os.environ['DATABASE_URL'] = (
            'postgresql://'
            + quote_plus(user)
            + ':'
            + quote_plus(password)
            + '@db:5432/'
            + quote_plus(db_name)
        )

configure_direct_database_url()
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.services.backup_service import BackupService
from django.contrib.auth import get_user_model
from django.db import connections

logger = logging.getLogger(__name__)

def wait_for_database():
    last_error = None
    for attempt in range(30):
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            return
        except Exception as exc:
            last_error = exc
            connections.close_all()
            time.sleep(2)
    raise RuntimeError(f"Database did not become ready for restore: {{last_error}}")

def run_restore():
    wait_for_database()
    User = get_user_model()
    owner_email = {owner_email!r}

    # Precise owner matching: find user by email, fallback to superuser
    target_user = None
    if owner_email:
        target_user = User.objects.filter(email=owner_email).first()
        if target_user:
            print(f"Found matching owner on target: {{owner_email}}")

    if not target_user:
        target_user = User.objects.filter(is_superuser=True).first() or User.objects.first()
        if target_user:
            print(f"No matching owner found. Assigning to fallback user: {{target_user.email}}")

    if not target_user:
        print("ERROR: No suitable user found on target server to own the restored service.", file=sys.stderr)
        sys.exit(1)

    try:
        svc = BackupService()
        # Restore and capture result
        svc._restore_service_from_file({backup_path!r}, owner=target_user)
        print("SUCCESS")
    except Exception as e:
        print(f"RESTORE_FAILED: {{str(e)}}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    run_restore()
"""

    def _restore_full_server(self, remote_backup_path):
        self._update(60, 'Installing Grid platform on target...')

        self.ssh.exec_command(
            "yes | NON_INTERACTIVE=1 bash /tmp/install.sh",
            timeout=3600,
        )
        hosting_path = self._target_hosting_path()
        quoted_hosting_path = shlex.quote(hosting_path)
        compose = (
            f"cd {quoted_hosting_path} && "
            "{ COMPOSE='docker compose'; "
            "docker compose version >/dev/null 2>&1 || COMPOSE='docker-compose'; "
            "$COMPOSE"
        )

        self._update(70, 'Stopping services for data restore...')
        self.ssh.exec_command(f"{compose} down -v; }}")

        self.ssh.exec_command(f"cp /tmp/.env.restore {quoted_hosting_path}/.env")

        remote_temp_dir = f"/tmp/restore_{self.transfer.id}"
        self.ssh.exec_command(f"mkdir -p {shlex.quote(remote_temp_dir)}")
        self.ssh.exec_command(f"tar -xzf {shlex.quote(remote_backup_path)} -C {shlex.quote(remote_temp_dir)}")

        self._update(75, 'Restoring database...')
        db_dump = f"{remote_temp_dir}/db_dump.sql"

        self.ssh.exec_command(f"{compose} up -d db; }}")
        time.sleep(20)

        self.ssh.exec_command(f"docker cp {shlex.quote(db_dump)} smsly-db:/tmp/dump.sql")

        db_user = _command_text(self.ssh.exec_command(
            f"grep POSTGRES_USER {quoted_hosting_path}/.env | cut -d= -f2"
        )).strip() or 'smsly'
        db_name = _command_text(self.ssh.exec_command(
            f"grep POSTGRES_DB {quoted_hosting_path}/.env | cut -d= -f2"
        )).strip() or 'smsly'

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", db_user):
            raise RuntimeError("Unsafe POSTGRES_USER value in target .env.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", db_name):
            raise RuntimeError("Unsafe POSTGRES_DB value in target .env.")

        from django.db import connection as _django_db_connection
        from psycopg2 import sql as pg_sql

        # Build the SQL with psycopg2.sql.Identifier so the database
        # identifier is quoted by the driver rather than via shell
        # string concatenation. We need a real cursor just to format
        # the Composable; the SQL is then shipped verbatim to the
        # remote ``psql`` via SSH.
        drop_query = pg_sql.SQL(
            "DROP DATABASE IF EXISTS {}; CREATE DATABASE {};"
        ).format(
            pg_sql.Identifier(db_name),
            pg_sql.Identifier(db_name),
        )
        try:
            with _django_db_connection.cursor() as _cur:
                drop_sql_str = drop_query.as_string(_cur)
        except Exception:  # pylint: disable=broad-exception-caught
            # Fall back to manual double-quoting if no live connection
            # is available. ``db_name`` is already validated against a
            # safe regex so this is purely defence-in-depth.
            escaped = db_name.replace('"', '""')
            drop_sql_str = (
                f'DROP DATABASE IF EXISTS "{escaped}"; '
                f'CREATE DATABASE "{escaped}";'
            )

        drop_cmd = (
            f"{compose} exec -T db psql -U {shlex.quote(db_user)} postgres "
            f"-c {shlex.quote(drop_sql_str)}"
            "; }"
        )
        self.ssh.exec_command(drop_cmd)

        restore_cmd = (
            f"{compose} exec -T db sh -c "
            + shlex.quote(
                f"psql -U {shlex.quote(db_user)} -d {shlex.quote(db_name)} < /tmp/dump.sql"
            )
            + "; }"
        )
        self.ssh.exec_command(restore_cmd)

        self._update(80, 'Restoring service data...')

        # Create restore script using list args to prevent shell injection
        restore_script = f"""
import os
import json
import subprocess
import glob

RESTORE_DIR = "{remote_temp_dir}"

def run(cmd):
    # Pass list directly to subprocess (shell=False)
    subprocess.run(cmd, check=True)

services_dir = os.path.join(RESTORE_DIR, "services")
if os.path.exists(services_dir):
    for tar_file in glob.glob(os.path.join(services_dir, "*.tar.gz")):
        print(f"Restoring {{tar_file}}...")
        svc_tmp = os.path.join(RESTORE_DIR, "svc_tmp")
        os.makedirs(svc_tmp, exist_ok=True)
        run(["tar", "-xzf", tar_file, "-C", svc_tmp])

        # Load Image
        run(["docker", "load", "-i", f"{{svc_tmp}}/image.tar"])

        # Volumes
        meta_path = os.path.join(svc_tmp, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                data = json.load(f)
            for vol in data.get('volumes', []):
                vname = vol['name']
                vfile = vol['filename']
                print(f"Restoring volume {{vname}}...")
                try:
                    run(["docker", "volume", "create", vname])
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.exception("docker volume create failed for %s: %s", vname, exc)

                run([
                    "docker", "run", "--rm", "-i",
                    "-v", f"{{vname}}:/dest",
                    "-v", f"{{svc_tmp}}:/src",
                    "alpine", "tar", "-xzf", f"/src/{{vfile}}", "-C", "/dest"
                ])

        run(["rm", "-rf", svc_tmp])
"""
        script_path = f"/tmp/restore_{self.transfer.id}.py"
        local_script = tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', prefix=f'restore_{self.transfer.id}_', delete=False
        )
        try:
            local_script.write(restore_script)
            local_script.close()
            self.ssh.upload_file(local_script.name, script_path)
        finally:
            os.unlink(local_script.name)

        self.ssh.exec_command(f"python3 {shlex.quote(script_path)}")

        self._update(90, 'Starting platform...')
        self.ssh.exec_command(f"{compose} up -d; }}")

        self._import_backup_key_on_target(remote_temp_dir)

        self.ssh.exec_command(f"rm -rf {remote_temp_dir} {remote_backup_path} {script_path} /tmp/.env.restore")

    def _restore_full_server_rest(self, remote_backup_path):
        """Restore full server via REST API (no SSH required).

        Uploads the backup to the target, extracts it, restores the
        database, and starts the platform — all via incoming REST endpoints.
        """
        import base64

        backup = self.transfer.source_backup or self.transfer.source_server_backup
        if not backup or not backup.file_path:
            raise ValueError("Backup file not found for FULL transfer.")

        local_path = backup.file_path
        self._update(62, 'Uploading backup to target server...')

        # Step 1: Upload the backup archive to target /tmp/
        remote_backup = f"/tmp/transfer_backup_{self.transfer.id}.tar.gz"
        file_size = os.path.getsize(local_path)
        self._log(f"Uploading {file_size} bytes to {remote_backup}")

        # For large files, upload in chunks via base64
        CHUNK_SIZE = 4 * 1024 * 1024  # 4MB chunks (base64 expands ~33%)
        with open(local_path, 'rb') as f:
            offset = 0
            chunk_index = 0
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                b64 = base64.b64encode(chunk).decode('ascii')
                self._node_api_request('incoming/upload-file', body={
                    'path': remote_backup,
                    'content_base64': b64,
                    'offset': offset,
                    'chunk_index': chunk_index,
                })
                offset += len(chunk)
                chunk_index += 1
                self._log(f"  Uploaded {offset}/{file_size} bytes")

        self._update(65, 'Extracting backup on target...')

        # Step 2: Extract the backup and read .env
        extract_dir = f"/tmp/restore_{self.transfer.id}"
        extract_script = f"""
import os, json, subprocess, glob

EXTRACT_DIR = "{extract_dir}"
BACKUP = "{remote_backup}"

os.makedirs(EXTRACT_DIR, exist_ok=True)
subprocess.run(["tar", "-xzf", BACKUP, "-C", EXTRACT_DIR], check=True)

# List extracted contents
for root, dirs, files in os.walk(EXTRACT_DIR):
    for f in files:
        print(os.path.join(root, f))
"""
        self._exec_on_target(extract_script)

        self._update(68, 'Restoring .env on target...')

        # Step 3: Read .env from extracted backup and push to target
        env_script = f"""
import os, json

EXTRACT_DIR = "{extract_dir}"
env_path = os.path.join(EXTRACT_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        print("ENV_CONTENT_START")
        print(f.read())
        print("ENV_CONTENT_END")
else:
    print("NO_ENV_FILE")
"""
        env_result = self._exec_on_target(env_script)
        env_output = env_result.get('stdout', '')

        # Parse env content from output
        env_content = ''
        if 'ENV_CONTENT_START' in env_output and 'ENV_CONTENT_END' in env_output:
            start = env_output.index('ENV_CONTENT_START') + len('ENV_CONTENT_START')
            end = env_output.index('ENV_CONTENT_END')
            env_content = env_output[start:end].strip()

        if env_content:
            # Write .env to target via upload-file
            b64_env = base64.b64encode(env_content.encode()).decode('ascii')
            self._node_api_request('incoming/upload-file',
body={
                'path': '/tmp/.env.restore',
                'content_base64': b64_env,
            })

            # Copy .env to hosting path
            write_env = """
import subprocess
subprocess.run(["cp", "/tmp/.env.restore", "/opt/smsly-hosting/.env"], check=True)
print("ENV_WRITTEN")
"""
            self._exec_on_target(write_env)

        self._update(72, 'Restoring database on target...')

        # Step 4: Restore database
        restore_db_script = f"""
import os, subprocess, re, json

EXTRACT_DIR = "{extract_dir}"
db_dump = os.path.join(EXTRACT_DIR, "db_dump.sql")

if not os.path.exists(db_dump):
    print("NO_DB_DUMP")
else:
    # Read DB credentials from .env
    env_path = "/opt/smsly-hosting/.env"
    db_user = "smsly"
    db_name = "smsly"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("POSTGRES_USER="):
                    db_user = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("POSTGRES_DB="):
                    db_name = line.split("=", 1)[1].strip().strip('"').strip("'")

    # Validate identifiers
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{{0,62}}", db_user):
        db_user = "smsly"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{{0,62}}", db_name):
        db_name = "smsly"

    # Drop and recreate database
    drop_sql = f'DROP DATABASE IF EXISTS "{{db_name}}"; CREATE DATABASE "{{db_name}}";'
    try:
        subprocess.run(
            ["docker", "exec", "smsly-hosting-db-1", "psql", "-U", db_user, "-d", "postgres", "-c", drop_sql],
            check=True, capture_output=True, text=True
        )
    except Exception:
        # Try alternative container name
        subprocess.run(
            ["docker", "exec", "smsly-db", "psql", "-U", db_user, "-d", "postgres", "-c", drop_sql],
            check=True, capture_output=True, text=True
        )

    # Copy dump to DB container and restore
    subprocess.run(["docker", "cp", db_dump, "smsly-hosting-db-1:/tmp/dump.sql"], check=True)
    restore_result = subprocess.run(
        ["docker", "exec", "smsly-hosting-db-1", "psql", "-U", db_user, "-d", db_name, "-f", "/tmp/dump.sql"],
        capture_output=True, text=True
    )
    if restore_result.returncode != 0:
        # Try alternative container name
        subprocess.run(["docker", "cp", db_dump, "smsly-db:/tmp/dump.sql"], check=True)
        subprocess.run(
            ["docker", "exec", "smsly-db", "psql", "-U", db_user, "-d", db_name, "-f", "/tmp/dump.sql"],
            check=True
        )
    print("DB_RESTORED")
"""
        db_result = self._exec_on_target(restore_db_script)
        if 'DB_RESTORED' not in db_result.get('stdout', ''):
            self._log(f"DB restore warning: {db_result.get('stdout', '')[:300]}")

        self._update(80, 'Restoring service data on target...')

        # Step 5: Restore service images and volumes
        restore_services_script = f"""
import os, json, subprocess, glob

EXTRACT_DIR = "{extract_dir}"
services_dir = os.path.join(EXTRACT_DIR, "services")

if not os.path.exists(services_dir):
    print("NO_SERVICES_DIR")
else:
    restored = 0
    for tar_file in glob.glob(os.path.join(services_dir, "*.tar.gz")):
        print(f"Restoring {{tar_file}}...")
        svc_tmp = os.path.join(EXTRACT_DIR, "svc_tmp")
        os.makedirs(svc_tmp, exist_ok=True)
        subprocess.run(["tar", "-xzf", tar_file, "-C", svc_tmp], check=True)

        # Load Docker image
        image_tar = os.path.join(svc_tmp, "image.tar")
        if os.path.exists(image_tar):
            subprocess.run(["docker", "load", "-i", image_tar], check=True)

        # Restore volumes
        meta_path = os.path.join(svc_tmp, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                data = json.load(f)
            for vol in data.get("volumes", []):
                vname = vol["name"]
                vfile = vol["filename"]
                vfile_path = os.path.join(svc_tmp, vfile)
                if os.path.exists(vfile_path):
                    try:
                        subprocess.run(["docker", "volume", "create", vname], check=True)
                    except Exception:
                        pass
                    subprocess.run([
                        "docker", "run", "--rm", "-i",
                        "-v", f"{{vname}}:/dest",
                        "-v", f"{{svc_tmp}}:/src",
                        "alpine", "tar", "-xzf", f"/src/{{vfile}}", "-C", "/dest"
                    ], check=True)

        subprocess.run(["rm", "-rf", svc_tmp], check=True)
        restored += 1

    print(f"SERVICES_RESTORED:{{restored}}")
"""
        self._exec_on_target(restore_services_script)

        self._update(88, 'Starting platform on target...')

        # Step 6: Start the platform
        start_script = """
import subprocess, os

hosting_path = "/opt/smsly-hosting"
os.chdir(hosting_path)

# Create required directories
os.makedirs("caddy-config", exist_ok=True)
os.makedirs("/opt/smsly-cache", exist_ok=True)

# Ensure networks exist
subprocess.run(["docker", "network", "inspect", "smsly-net"], capture_output=True)
subprocess.run(["docker", "network", "create", "smsly-net"], capture_output=True)
subprocess.run(["docker", "network", "inspect", "smsly-proxy"], capture_output=True)
subprocess.run(["docker", "network", "create", "smsly-proxy"], capture_output=True)

# Detect compose file
compose_file = None
for candidate in [
    "infrastructure/docker/docker-compose.agent-lite.yml",
    "docker-compose.prod.yml",
    "docker-compose.yml",
]:
    if os.path.exists(candidate):
        compose_file = candidate
        break

if compose_file:
    subprocess.run(["docker", "compose", "-f", compose_file, "up", "-d", "--build"], check=True)
else:
    subprocess.run(["docker", "compose", "up", "-d", "--build"], check=True)

print("PLATFORM_STARTED")
"""
        self._exec_on_target(start_script)

        # Step 7: Cleanup
        self._exec_on_target(f"""
import subprocess, os
subprocess.run(["rm", "-rf", "{extract_dir}", "{remote_backup}", "/tmp/.env.restore"], check=False)
print("CLEANUP_DONE")
""")

    def _import_backup_key_on_target(self, remote_temp_dir: str) -> None:
        """Import the source's BACKUP_ENCRYPTION_KEY on the target.

        No-op when the key export bundle was not shipped (either
        BACKUP_ENCRYPTION_KEY was not set on the source, or the
        target is not configured for a FULL transfer). When the
        bundle is present, runs a small Python script inside the
        target's backend container that calls
        ``BackupService.import_backup_key`` so the source's
        historical backups remain readable on the target.
        """
        if self.transfer.transfer_type != 'FULL':
            return
        if not self.ssh:
            return
        bundle_check = _command_text(self.ssh.exec_command(
            "test -f /tmp/key_export.json && echo PRESENT || echo MISSING",
            raise_on_error=False,
        )).strip()
        if "PRESENT" not in bundle_check:
            return
        try:
            backend_container = self._find_remote_backend_container(required=True)
        except Exception as exc:
            self._log(
                f"Could not find backend container for key import: {exc}. "
                "Historical backups from the source will need to be "
                "manually imported on the target."
            )
            return
        try:
            self._wait_for_remote_backend_ready(backend_container)
        except Exception as exc:
            self._log(
                f"Backend container did not become ready for key import: {exc}. "
                "Continuing without key migration."
            )
            return
        safe_backend_container = shlex.quote(backend_container)
        bundle = _command_text(self.ssh.exec_command("cat /tmp/key_export.json")).strip()
        if not bundle:
            self._log("Key export bundle on target is empty — skipping import.")
            return
        try:
            parsed = json.loads(bundle)
            key_id = parsed.get('key_id', '')
            parsed.get('source_label', 'migrated-from-unknown')
        except Exception as exc:
            self._log(f"Could not parse key export bundle: {exc} — skipping import.")
            return
        if not key_id:
            self._log("Key export bundle missing key_id — skipping import.")
            return
        key_material = parsed.get('key_material', '')
        if not key_material:
            self._log("Key export bundle missing key_material — skipping import.")
            return
        import_script = """
import os
import sys
import json
import django

for candidate in (os.getcwd(), '/app', '/app/backend'):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.services.backup_service import BackupService

KEY_EXPORT_PATH = '/tmp/key_export.json'

def run():
    if not os.path.exists(KEY_EXPORT_PATH):
        print('ERROR: key export not found at ' + KEY_EXPORT_PATH, file=sys.stderr)
        sys.exit(1)
    try:
        with open(KEY_EXPORT_PATH) as f:
            bundle = json.load(f)
    except Exception as exc:
        print(f'ERROR: failed to read key export: {exc}', file=sys.stderr)
        sys.exit(1)
    key_id = bundle.get('key_id', '')
    key_material = bundle.get('key_material', '')
    label = bundle.get('source_label', 'migrated-from-unknown')
    if not key_id or not key_material:
        print('ERROR: key export missing key_id or key_material', file=sys.stderr)
        sys.exit(1)
    try:
        result = BackupService.import_backup_key(
            key_id=key_id,
            key_material=key_material,
            label=label,
        )
        print(f"IMPORTED key_id={result['key_id']} fingerprint={result['fingerprint']} created={result['created']}")
    except Exception as exc:
        print(f'ERROR: failed to import key: {exc}', file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    run()
"""
        script_path = f"/tmp/import_key_{self.transfer.id}.py"
        local_script = tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', prefix=f'import_key_{self.transfer.id}_', delete=False
        )
        try:
            local_script.write(import_script)
            local_script.close()
            self.ssh.upload_file(local_script.name, script_path)
        finally:
            os.unlink(local_script.name)
        self.ssh.exec_command(
            f"docker cp {shlex.quote(script_path)} "
            f"{safe_backend_container}:/tmp/import_key.py"
        )
        result = _command_text(self.ssh.exec_command(
            f"docker exec {safe_backend_container} python3 /tmp/import_key.py"
        ))
        if "IMPORTED" not in result or "ERROR" in result:
            self._log(
                f"BACKUP_ENCRYPTION_KEY import on target did not confirm success: {result}"
            )
        else:
            self._log(
                f"Imported source BACKUP_ENCRYPTION_KEY on target: {result.strip()}"
            )
        self.ssh.exec_command(
            f"docker exec -u 0 {safe_backend_container} sh -lc "
            + shlex.quote("rm -f /tmp/import_key.py /tmp/key_export.json || true"),
            raise_on_error=False,
        )
        self.ssh.exec_command(
            f"rm -f {shlex.quote(script_path)}",
            raise_on_error=False,
        )

    def _stop_source_service(self):
        """Stop and remove the service container on the source node via REST.

        Called immediately before DNS cutover. For FULL transfers the
        source host is being decommissioned, so this is a no-op.
        """
        if self.transfer.transfer_type != 'SERVICE' or not self.transfer.service:
            return
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            container = client.containers.get(self.transfer.service.name)
            container.stop(timeout=10)
            container.remove()
        except Exception:
            pass

    def _dns_cutover(self):
        self._update(85, 'DNS cutover: updating records...')

        target_ip = self.transfer.target_server_ip
        config = PlatformConfig.load()

        if config.cloudflare_api_token and config.domain:
            try:
                if self.transfer.transfer_type == 'FULL':
                    self._update_cloudflare_dns(config.domain, target_ip, config.cloudflare_api_token)
                elif self.transfer.service and self.transfer.service.public_domain:
                    target_server = self._target_server_record()
                    is_lite = getattr(target_server, 'is_lite_agent', False) if target_server else False
                    # For lite agents, create a per-service A record so the domain
                    # resolves directly to the target (Traefik routes via labels).
                    # For full platform targets, keep DNS pointing at the master
                    # and use WireGuard routing through the master's Caddy.
                    if is_lite:
                        self._update_service_a_record(
                            self.transfer.service.public_domain,
                            target_ip,
                            config.cloudflare_api_token,
                        )
            except Exception as e:
                logger.error(f"Cloudflare update failed: {e}")

        if self.transfer.transfer_type == 'FULL':
            config.server_ip = target_ip
            config.save()

            # ── Full migration: reconnect all managed nodes to new master ──
            if self.transfer.target_public_domain:
                self._migrate_managed_nodes_wireguard(config)

        # Caddyfile regeneration removed from here — at this point service.server
        # is still the source (primary), so _remote_upstream_url_for_service()
        # returns empty, causing routing to local Traefik where the container
        # may no longer exist.  The correct routing (via WireGuard mesh IP) is
        # applied in _complete() after service.server is set to the target.

    def _interconnect_servers(self):
        """
        Automatically interconnect the source and target servers using the WireGuard Mesh.
        This enables them to discover and display services to each other.
        """
        from ..models_core import ManagedServer
        from ..models_mesh import MeshNetwork
        from .wireguard_service import WireGuardService

        self._update(96, 'Configuring mesh interconnect between source and target servers...')
        try:
            # Add the target server as a ManagedServer (if not already managed).
            target_server = self._target_server_record()
            if not target_server:
                owner = None
                if self.transfer.service and self.transfer.service.owner:
                    owner = self.transfer.service.owner
                else:
                    from django.contrib.auth import get_user_model
                    owner = get_user_model().objects.first()

                if not owner:
                    raise ValueError("No valid user available to own the interconnected target server.")

                target_server = ManagedServer.objects.create(
                    name=f"TransferTarget-{self.transfer.target_server_ip}",
                    host=self.transfer.target_server_ip,
                    project=getattr(self.transfer.service, "project", None),
                    owner=owner,
                    ssh_key=self.transfer.target_ssh_key,
                    ssh_password=self.transfer.target_ssh_password,
                    status=ManagedServer.Status.ONLINE,
                )
            elif target_server.status != ManagedServer.Status.ONLINE:
                target_server.status = ManagedServer.Status.ONLINE
                target_server.save(update_fields=['status', 'updated_at'])
            if target_server and not (target_server.ssh_key or target_server.ssh_password):
                target_server.ssh_key = self.transfer.target_ssh_key
                target_server.ssh_password = self.transfer.target_ssh_password
                target_server.save(update_fields=['ssh_key', 'ssh_password', 'updated_at'])

            if self.transfer.service and self.transfer.service.project and not target_server.project_id:
                target_server.project = self.transfer.service.project
                target_server.save(update_fields=['project', 'updated_at'])

            # Reuse the canonical default mesh. Creating a transfer-specific mesh
            # with the same wg0 interface overwrites the existing host config and
            # leaves Caddy/API routing pointed at stale WireGuard addresses.
            ensure_result = WireGuardService.ensure_server_in_default_mesh(
                target_server,
                deploy_async=False,
            )
            mesh = MeshNetwork.objects.get(id=ensure_result["mesh"])
            results = WireGuardService.deploy_full_mesh(mesh)
            if results.get("failed"):
                logger.warning(
                    "Mesh interconnect configured but deployment had failures: %s",
                    results["failed"],
                )
                return

            logger.info(f"Successfully configured mesh interconnect between local and {self.transfer.target_server_ip}")
        except Exception as e:
            # Non-fatal error, we still want the transfer to be marked complete
            logger.warning(f"Failed to interconnect servers via WireGuard mesh: {e}")

    def _verify(self):
        self._update(95, 'Verifying services on target server...')

        # Interconnect old and new servers automatically so they can communicate
        self._interconnect_servers()
        self._verify_between_servers()

        # Retry loop: containers and health endpoints need warm-up time
        # after deploy.  A fixed sleep is fragile on slow disks/networks.
        deadline = time.monotonic() + 120
        last_error = None
        while time.monotonic() < deadline:
            if self.transfer.transfer_type == 'FULL':
                target_server = self._target_server_record()
                health_ip = (
                    str(getattr(target_server, 'wg_address', '') or '').strip()
                    or self.transfer.target_server_ip
                )
                url = f"http://{health_ip}:8000/health"
                try:
                    resp = requests.get(url, timeout=10)
                    if 200 <= resp.status_code < 500:
                        logger.info("Target health check passed (HTTP %s)", resp.status_code)
                        break
                    last_error = RuntimeError(
                        f"Target health check returned HTTP {resp.status_code}"
                    )
                except requests.RequestException as e:
                    last_error = e
            elif self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
                try:
                    container_name = self.transfer.service.name
                    status_result = self._node_api_request(
                        'incoming/container-status',
                        method='GET',
                        params={'container_name': container_name},
                    )
                    if status_result.get('running'):
                        logger.info("Service container %s verified running on target", container_name)
                        break
                    last_error = RuntimeError(
                        f"Service container {container_name} is not running on target"
                    )
                except Exception as e:
                    last_error = e
            else:
                break  # No verification needed for other transfer types

            time.sleep(5)

        else:
            # Loop exhausted without success
            raise RuntimeError(
                f"Verification timed out after 120 s: {last_error}"
            ) from last_error

    def _verify_between_servers(self):
        """
        Verify connectivity between source and target servers.
        Both SERVICE and FULL transfers now use the REST API.
        """
        source_ip = str(getattr(self.transfer, 'source_server_ip', '') or '').strip()
        target_ip = str(getattr(self.transfer, 'target_server_ip', '') or '').strip()
        if not source_ip or not target_ip:
            return

        # Both SERVICE and FULL transfers use REST API — verify target is reachable
        try:
            result = self._node_api_request('incoming/ensure-docker', timeout=10)
            if result.get('docker_available'):
                logger.info("Connectivity check passed: controller -> %s (API reachable)", target_ip)
                return
        except Exception as exc:
            logger.warning("Connectivity check failed: controller -> %s (API unreachable: %s)", target_ip, exc)

        # Fallback: check TCP/22 for FULL transfers (SSH may be needed for emergency)
        if self.transfer.transfer_type == 'FULL':
            try:
                with socket.create_connection((target_ip, 22), timeout=5):
                    logger.info("Connectivity check passed: controller -> %s:22 (SSH fallback)", target_ip)
                    return
            except OSError as exc:
                logger.warning("SSH fallback also failed: controller -> %s:22 (%s)", target_ip, exc)

    def _regenerate_master_caddyfile(self):
        """Regenerate and reload the Caddyfile on the master node.

        After a service is transferred to a remote node, the master's
        Caddyfile must be updated to route traffic for that service's
        domain to the remote node via WireGuard mesh instead of the
        local Traefik instance.
        """
        from services.caddy_manager import apply_caddyfile, generate_caddyfile
        config = PlatformConfig.load()
        content = generate_caddyfile(config)
        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            self._log("Caddyfile regenerated on master node for remote service routing.")
        else:
            raise RuntimeError(
                f"Caddyfile regeneration failed after transfer: {result.get('message')}. "
                "Reverting transfer."
            )

    def _complete(self):
        self.transfer.status = 'COMPLETED'
        self.transfer.completed_at = timezone.now()
        # TRANSFER_ROLLBACK_HOURS: override the default 48 h rollback window.
        # Longer windows give more time to validate the cutover before the
        # source is cleaned up; shorter windows reduce the dual-running cost.
        rollback_hours = int(os.environ.get("TRANSFER_ROLLBACK_HOURS", "48"))
        self.transfer.rollback_deadline = timezone.now() + timedelta(hours=max(1, rollback_hours))
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''

        if self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            from ..models_core import ManagedServer
            target_server = ManagedServer.objects.filter(
                Q(host=self.transfer.target_server_ip) |
                Q(private_ip=self.transfer.target_server_ip)
            ).first()
            if target_server:
                self.transfer.service.server = target_server
                self.transfer.service.active_target_type = (
                    'lite_agent'
                    if getattr(target_server, 'is_lite_agent', False)
                    else 'remote'
                )
                self.transfer.service.active_host_ip = (
                    getattr(target_server, 'wg_address', None)
                    or target_server.private_ip
                    or target_server.host
                    or self.transfer.target_server_ip
                )
                self.transfer.service.active_runtime_id = self.transfer.service.name

                # ── Cross-platform migration: remap domain to target platform ──
                domain_fields = self._remap_service_domain_for_target(target_server)
                update_fields = ['server', 'active_target_type', 'active_host_ip', 'active_runtime_id', *domain_fields]

                self.transfer.service.save(update_fields=update_fields)

                # Regenerate Caddyfile on the master node so it knows to
                # route traffic for this service to the remote node via
                # WireGuard mesh.  Without this, Caddy proxies to the local
                # Traefik where the service doesn't exist,
                # causing HTTP 502 errors.
                self._regenerate_master_caddyfile()

        self.transfer.save()
        self._update(100, 'Transfer complete!')

    def _remap_service_domain_for_target(self, target_server):
        """Regenerate public_domain to match target platform's base domain."""
        fields = []
        svc = self.transfer.service
        old_domain = (svc.public_domain or '').strip()

        # 1. Explicit target domain from transfer form
        new_base = (self.transfer.target_public_domain or '').strip()

        # 2. Auto-detect from target server via REST API
        if not new_base:
            try:
                domain_script = (
                    "import os; "
                    "print(os.environ.get('DOMAIN', '') or '')"
                )
                exec_result = self._exec_on_target(domain_script)
                new_base = (exec_result.get('stdout') or '').strip()
            except Exception:
                pass

        # 3. Fallback to target server host
        if not new_base and target_server:
            new_base = target_server.host or ''

        if not new_base or '.' not in new_base:
            return fields

        # Extract subdomain from old domain, or use service name
        old_base = svc.default_public_base_domain()
        subdomain = old_domain.replace(f'.{old_base}', '') if old_domain.endswith(f'.{old_base}') else ''
        if not subdomain or subdomain == old_domain:
            subdomain = svc.name.lower().replace(' ', '-')

        new_domain = f"{subdomain}.{new_base}"
        if new_domain != old_domain:
            svc.public_domain = new_domain
            fields.append('public_domain')
            self._log(f"Domain remapped: {old_domain} → {new_domain}")

        return fields

    def _migrate_managed_nodes_wireguard(self, config):
        """Reconnect all managed servers to the new master's WireGuard mesh."""
        from ..models_core import ManagedServer
        from .wireguard_service import WireGuardService

        self._update(88, 'Reconnecting managed nodes to new master...')
        nodes = ManagedServer.objects.filter(
            is_primary=False, status=ManagedServer.Status.ONLINE,
        ).exclude(host=self.transfer.target_server_ip)

        reconnected = 0
        for node in nodes:
            try:
                WireGuardService.ensure_server_in_default_mesh(node, deploy_async=False)
                reconnected += 1
            except Exception as exc:
                logger.warning("Failed to reconnect node %s: %s", node.name, exc)

        self._log(f"Reconnected {reconnected}/{nodes.count()} managed nodes to new master")
        return reconnected

    def _stop_target_service_on_rollback(self):
        """Stop and remove the service container on the target during rollback.

        After a successful transfer the target keeps the now-orphaned
        container running. Rollback points Caddy back to the source, but
        leaves the target container still answering requests on the local
        Traefik — wasted CPU and a confusing "two live copies" state. Stop
        and remove it.

        Skipped for FULL transfers (would need a confirm dialog — the
        target is the new master and tearing it down is destructive).
        """
        if self.transfer.transfer_type != 'SERVICE' or not self.transfer.service:
            return
        try:
            self._node_api_request('incoming/stop-container', body={
                'container_name': self.transfer.service.name,
            })
        except Exception as exc:
            logger.warning("Failed to stop target service during rollback: %s", exc)

    def rollback(self):
        if not self.transfer.can_rollback:
            raise ValueError('Rollback not allowed')
        if self.transfer.status != 'COMPLETED':
            raise ValueError('Rollback is only available for completed transfers')
        if self.transfer.rollback_deadline and timezone.now() > self.transfer.rollback_deadline:
            self.transfer.can_rollback = False
            self.transfer.save(update_fields=['can_rollback'])
            raise ValueError('Rollback window has expired')

        config = PlatformConfig.load()
        if config.cloudflare_api_token and config.domain:
            if self.transfer.transfer_type == 'FULL':
                self._update_cloudflare_dns(config.domain, self.transfer.source_server_ip, config.cloudflare_api_token)
            elif self.transfer.service and self.transfer.service.public_domain:
                target_server = self._target_server_record()
                is_lite = getattr(target_server, 'is_lite_agent', False) if target_server else False
                if is_lite:
                    self._delete_service_a_record(
                        self.transfer.service.public_domain,
                        config.cloudflare_api_token,
                    )

        if self.transfer.transfer_type == 'FULL':
            config.server_ip = self.transfer.source_server_ip
            config.save()
        elif self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            from ..models_core import ManagedServer
            source_server = ManagedServer.objects.filter(host=self.transfer.source_server_ip).first()
            self.transfer.service.server = source_server
            self.transfer.service.save(update_fields=['server'])

            # Tear down the now-orphaned target container and revert the
            # env-var snapshot taken during remap BEFORE Caddy is told to
            # route traffic back to the source.
            self._stop_target_service_on_rollback()
            self._revert_target_platform_env()

            # Regenerate Caddyfile so routing points back to the source
            self._regenerate_master_caddyfile()

        self.transfer.status = 'ROLLED_BACK'
        self.transfer.can_rollback = False
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''
        self.transfer.save()

    def _update(self, percent, step):
        self.transfer.progress_percent = percent
        self.transfer.current_step = step
        self.transfer.save(update_fields=['progress_percent', 'current_step'])
        self._log(step)

    def _handle_failure(self, error):
        self.transfer.status = 'FAILED'
        self.transfer.error_message = _redact_transfer_text(str(error))[:TRANSFER_ERROR_LIMIT]
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''
        self.transfer.source_ssh_key = ''
        self.transfer.source_ssh_password = ''
        self.transfer.save(update_fields=['status', 'error_message', 'target_ssh_key', 'target_ssh_password', 'source_ssh_key', 'source_ssh_password'])
        self._log(f"CRITICAL FAILURE: {error}")

    def _generate_docker_run_command(self, service, metadata):
        name = service.name
        image = metadata.get('docker_image') or service.docker_image
        if not image:
            raise RuntimeError(
                f"No Docker image was available in the backup for service {service.name}. "
                "Use remote Git deployment or provide service.docker_image for this service."
            )

        config = PlatformConfig.load()

        # Build command parts safely
        run_args = ["docker", "run", "-d", "--name", name, "--restart", "unless-stopped"]

        net = "smsly-net"
        run_args.extend(["--network", net])

        env_vars = metadata.get('env_vars', [])
        for e in env_vars:
            # -e KEY=VAL
            run_args.extend(["-e", f"{e['key']}={e['value']}"])

        domain = service.public_domain
        port = service.internal_port

        # Labels
        run_args.extend(["-l", "traefik.enable=true"])
        run_args.extend(["-l", f"traefik.docker.network={net}"])
        run_args.extend(["-l", f"traefik.http.routers.{name}.rule=Host(`{domain}`)"])
        run_args.extend(["-l", f"traefik.http.routers.{name}.service={name}"])
        run_args.extend(["-l", f"traefik.http.services.{name}.loadbalancer.server.port={port}"])

        enable_traefik_tls = (
            str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        if config.use_ssl and enable_traefik_tls:
            run_args.extend(["-l", f"traefik.http.routers.{name}.entrypoints=websecure"])
            run_args.extend(["-l", f"traefik.http.routers.{name}.tls=true"])
            run_args.extend(["-l", f"traefik.http.routers.{name}.tls.certresolver=letsencrypt"])
        else:
            run_args.extend(["-l", f"traefik.http.routers.{name}.entrypoints=web"])
            if config.use_ssl:
                middleware_name = f"{name}-forwarded-https"
                run_args.extend(["-l", f"traefik.http.routers.{name}.middlewares={middleware_name}"])
                run_args.extend([
                    "-l",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Proto=https",
                ])
                run_args.extend([
                    "-l",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Port=443",
                ])
                run_args.extend([
                    "-l",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Ssl=on",
                ])

        # Volumes
        if 'volumes' in metadata:
            for vol in metadata['volumes']:
                run_args.extend(["-v", f"{vol['name']}:{vol['mount_path']}"])

        run_args.append(image)

        # Construct final command string safely
        safe_run = " ".join(shlex.quote(arg) for arg in run_args)

        safe_net = shlex.quote(net)
        net_cmd = (
            f"docker network inspect {safe_net} >/dev/null 2>&1 "
            f"|| docker network create {safe_net} >/dev/null"
        )
        rm_cmd = f"docker rm -f {shlex.quote(name)} || true"

        return f"{net_cmd} && {rm_cmd} && {safe_run}"

    def _update_cloudflare_dns(self, domain, ip, token):
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        base_url = "https://api.cloudflare.com/client/v4"

        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': domain}, timeout=30)
        if not resp.ok:
            return

        zones = resp.json().get('result')
        if not zones:
            return
        zone_id = zones[0]['id']

        records_to_update = ['@', '*']

        for name in records_to_update:
            search_name = f"{name}.{domain}" if name != '@' else domain
            resp = requests.get(f"{base_url}/zones/{zone_id}/dns_records",
                                headers=headers,
                                params={'type': 'A', 'name': search_name}, timeout=30)
            if resp.ok:
                results = resp.json().get('result', [])
                for record in results:
                    update_url = f"{base_url}/zones/{zone_id}/dns_records/{record['id']}"
                    payload = {
                        'type': 'A',
                        'name': record['name'],
                        'content': ip,
                        'ttl': record['ttl'],
                        'proxied': record['proxied']
                    }
                    requests.put(update_url, headers=headers, json=payload, timeout=30)

    def _update_service_a_record(self, public_domain, target_ip, token):
        """Create/update a specific A record for the service subdomain, leaving the wildcard intact."""
        config = PlatformConfig.load()
        platform_domain = config.domain
        if not platform_domain:
            return
        domain = str(public_domain or '').strip().lower()
        if not domain.endswith('.' + platform_domain):
            return
        name = domain[:-(len(platform_domain) + 1)]
        if not name:
            return
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        base_url = "https://api.cloudflare.com/client/v4"
        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': platform_domain}, timeout=30)
        if not resp.ok:
            return
        zones = resp.json().get('result')
        if not zones:
            return
        zone_id = zones[0]['id']
        search = requests.get(
            f"{base_url}/zones/{zone_id}/dns_records",
            headers=headers,
            params={'type': 'A', 'name': domain},
            timeout=30,
        )
        existing = search.json().get('result', []) if search.ok else []
        payload = {'type': 'A', 'name': name, 'content': target_ip, 'ttl': 1, 'proxied': False}
        if existing:
            record_id = existing[0]['id']
            requests.put(
                f"{base_url}/zones/{zone_id}/dns_records/{record_id}",
                headers=headers, json=payload, timeout=30,
            )
        else:
            requests.post(
                f"{base_url}/zones/{zone_id}/dns_records",
                headers=headers, json=payload, timeout=30,
            )

    def _delete_service_a_record(self, public_domain, token):
        """Delete the specific A record for a service subdomain."""
        config = PlatformConfig.load()
        platform_domain = config.domain
        if not platform_domain:
            return
        domain = str(public_domain or '').strip().lower()
        if not domain.endswith('.' + platform_domain):
            return
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        base_url = "https://api.cloudflare.com/client/v4"
        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': platform_domain}, timeout=30)
        if not resp.ok:
            return
        zones = resp.json().get('result')
        if not zones:
            return
        zone_id = zones[0]['id']
        search = requests.get(
            f"{base_url}/zones/{zone_id}/dns_records",
            headers=headers,
            params={'type': 'A', 'name': domain},
            timeout=30,
        )
        if search.ok:
            for record in search.json().get('result', []):
                requests.delete(
                    f"{base_url}/zones/{zone_id}/dns_records/{record['id']}",
                    headers=headers, timeout=30,
                )
