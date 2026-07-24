"""TransferMixin — extracted from ServerTransferService for lifecycle methods."""

import json
import logging
import os
import shlex
import socket
import time
from datetime import timedelta

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
    _safe_service_name,
    get_transfer_log_limit,
)

logger = logging.getLogger(__name__)


class TransferMixin:
    """Mixin providing the transfer lifecycle methods (_stop_source_service → rollback)."""

    def _stop_source_service(self):
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

            if self.transfer.target_public_domain:
                self._migrate_managed_nodes_wireguard(config)

    def _interconnect_servers(self):
        from ....models.core import ManagedServer
        from ....models.mesh import MeshNetwork
        from ...wireguard_service import WireGuardService

        self._update(96, 'Configuring mesh interconnect between source and target servers...')
        try:
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
            logger.warning(f"Failed to interconnect servers via WireGuard mesh: {e}")

    def _verify(self):
        self._update(95, 'Verifying services on target server...')

        self._interconnect_servers()
        self._verify_between_servers()

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
                break

            time.sleep(5)

        else:
            raise RuntimeError(
                f"Verification timed out after 120 s: {last_error}"
            ) from last_error

    def _verify_between_servers(self):
        source_ip = str(getattr(self.transfer, 'source_server_ip', '') or '').strip()
        target_ip = str(getattr(self.transfer, 'target_server_ip', '') or '').strip()
        if not source_ip or not target_ip:
            return

        try:
            result = self._node_api_request('incoming/ensure-docker', timeout=10)
            if result.get('docker_available'):
                logger.info("Connectivity check passed: controller -> %s (API reachable)", target_ip)
                return
        except Exception as exc:
            logger.warning("Connectivity check failed: controller -> %s (API unreachable: %s)", target_ip, exc)

        if self.transfer.transfer_type == 'FULL':
            try:
                with socket.create_connection((target_ip, 22), timeout=5):
                    logger.info("Connectivity check passed: controller -> %s:22 (SSH fallback)", target_ip)
                    return
            except OSError as exc:
                logger.warning("SSH fallback also failed: controller -> %s:22 (%s)", target_ip, exc)

    def _regenerate_master_caddyfile(self):
        from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile
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
        rollback_hours = int(os.environ.get("TRANSFER_ROLLBACK_HOURS", "48"))
        self.transfer.rollback_deadline = timezone.now() + timedelta(hours=max(1, rollback_hours))
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''

        if self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            from ....models.core import ManagedServer
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

                domain_fields = self._remap_service_domain_for_target(target_server)
                update_fields = ['server', 'active_target_type', 'active_host_ip', 'active_runtime_id', *domain_fields]

                self.transfer.service.save(update_fields=update_fields)

                self._regenerate_master_caddyfile()

        self.transfer.save()
        self._update(100, 'Transfer complete!')

    def _remap_service_domain_for_target(self, target_server):
        fields = []
        svc = self.transfer.service
        old_domain = (svc.public_domain or '').strip()

        new_base = (self.transfer.target_public_domain or '').strip()

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

        if not new_base and target_server:
            new_base = target_server.host or ''

        if not new_base or '.' not in new_base:
            return fields

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
        from ....models.core import ManagedServer
        from ...wireguard_service import WireGuardService

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
            from ....models.core import ManagedServer
            source_server = ManagedServer.objects.filter(host=self.transfer.source_server_ip).first()
            self.transfer.service.server = source_server
            self.transfer.service.save(update_fields=['server'])

            self._stop_target_service_on_rollback()
            self._revert_target_platform_env()

            self._regenerate_master_caddyfile()

        self.transfer.status = 'ROLLED_BACK'
        self.transfer.can_rollback = False
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''
        self.transfer.save()
