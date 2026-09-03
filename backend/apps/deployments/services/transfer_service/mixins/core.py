"""CoreMixin extracted from ServerTransferService."""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shlex
import socket
import time

import requests
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.deployments.models.core import PlatformConfig

from ...backup_service import BackupService
from ..helpers import (
    TRANSFER_ERROR_LIMIT,
    _command_text,
    _redact_transfer_text,
    _safe_backup_basename,
    _safe_service_name,
    get_transfer_log_limit,
)

logger = logging.getLogger(__name__)


class CoreMixin:
    def __init__(self, transfer):
        self.transfer = transfer
        self.ssh = None
        self._uploaded_remote_backup_path = None
        self._target_transfer_id = None

    def _log(self, message):
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
        ip = (self.transfer.target_server_ip or '').strip()
        if not ip:
            return True
        local_ips = {'127.0.0.1', 'localhost', ''}
        try:
            cfg = PlatformConfig.load()
            if cfg and cfg.server_ip:
                local_ips.add(cfg.server_ip.strip())
        except Exception as exc:
            logger.debug("Failed to load PlatformConfig for local IP check: %s", exc)
        return ip in local_ips or ip.startswith('10.100.0.')

    def _node_api_url(self):
        ip = self.transfer.target_server_ip or '127.0.0.1'
        return f"http://{ip}"

    def _candidate_node_urls(self) -> list[str]:
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

        if wg_ip and wg_ip != target_ip:
            add(f"http://{wg_ip}")
            add(f"http://{wg_ip}:8090")

        add(f"http://{target_ip}")
        add(f"http://{target_ip}:8090")

        if host and host != target_ip:
            add(f"http://{host}")
            add(f"http://{host}:8090")

        return urls

    def _node_api_request(self, action, method='POST', body=None, params=None, timeout=120):
        target_ip = self.transfer.target_server_ip
        if not target_ip:
            raise RuntimeError("target_server_ip not set on transfer")

        transfer_id = self._target_transfer_id or str(self.transfer.id)
        path = f"/api/v1/transfers/{transfer_id}/{action}/"

        sig_path = path
        if params:
            from urllib.parse import urlencode
            qs = urlencode(params)
            sig_path = f"{path}?{qs}"

        body_bytes = json.dumps(body).encode() if body else b''

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
        from ....models.core import ManagedServer

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
            payload = f"POST|{path}|{timestamp}|{nonce}|{body_hash}"
            signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            headers['X-Request-Timestamp'] = timestamp
            headers['X-Request-Nonce'] = nonce
            headers['X-Gateway-Signature-V2'] = signature

        return headers

    def _sync_target_dashboard(self):
        try:
            path = "/api/v1/transfers/register-incoming/"
            server = self._target_server_record()
            if not server:
                self._log(f"Warning: No ManagedServer record found for {self.transfer.target_server_ip}. Sync skipped.")
                return

            from ...remote_orchestrator import RemoteOrchestrator
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
        self._update(5, 'Pre-flight: checking target server...')

        if not self._target_is_local():
            result = self._node_api_request('incoming/ensure-docker')
            if not result.get('docker_available'):
                self._log("Docker not detected on target — will be installed by the platform.")

        # PRE-TRANSFER SAFETY BACKUP: create a non-transfer safety backup
        # of the service's current state BEFORE the transfer backup. If the
        # transfer fails mid-way (source container stopped, DNS cut over,
        # restore crashed), this backup is the rollback point. The transfer
        # backup itself is a separate artifact that may be partially
        # uploaded/extracted on the target.
        if self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            try:
                safety = backup_svc = BackupService().backup_service(
                    self.transfer.service.id,
                    backup_type='PRE_TRANSFER',
                )
                self.transfer.metadata = self.transfer.metadata or {}
                self.transfer.metadata['safety_backup_id'] = str(safety.id)
                self.transfer.save(update_fields=['metadata'])
                self._log(f"Safety backup created: {safety.id}")
            except Exception as exc:
                self._log(f"Safety backup failed (non-fatal, continuing): {exc}")

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
            # FULL server transfer: pass backup_type='SERVER_TRANSFER'
            # so secrets are included in the backup (the target needs
            # real values to hydrate services — masked '********' values
            # would break every restored service's DB/Redis/API keys).
            backup = backup_svc.backup_server(
                backup_type='SERVER_TRANSFER',
            )
            self.transfer.source_server_backup = backup
            self.transfer.save(update_fields=['source_server_backup'])

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
