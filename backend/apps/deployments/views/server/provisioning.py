"""
Provisioning mixins for ManagedServerViewSet.
"""

import base64
import hashlib
import hmac as hmac_mod
import json as json_mod
import logging
import os
import secrets
import time

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models as db_models
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action, throttle_classes
from rest_framework.response import Response

from ...models.servers import ManagedServer
from .serializers import (
    ManagedServerProvisionSerializer,
    ManagedServerSerializer,
    ServerProvisionThrottle,
)

logger = logging.getLogger(__name__)


class ProvisioningMixin:

    @action(detail=False, methods=["post"], url_path="generate-key")
    def generate_key(self, request):
        from apps.deployments.services.provisioner.helpers.ssh import _generate_ed25519_keypair
        priv_key_pem, pub_key_line = _generate_ed25519_keypair()
        return Response(
            {
                "private_key": priv_key_pem,
                "public_key": pub_key_line,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="provision-token")
    def provision_token(self, request):
        """Create a ManagedServer, pre-provision VPN + DNS, and return a
        bootstrap command the user runs on the target server.

        Flow:
        1. User fills name, host, node_type, infra selections
        2. Master creates ManagedServer (PENDING), assigns node_number,
           computes node_domain (grid{N}), sets up WireGuard peer + DNS
        3. Master returns bootstrap command with server_id, WG keys, etc.
        4. User runs bootstrap on target → node installs Grid, registers
        5. If no heartbeat within 12 hours, master purges the record
        """
        name = request.data.get("name", "").strip()
        host = request.data.get("host", "").strip()
        node_type = request.data.get("node_type", "node")
        is_lite_agent = request.data.get("is_lite_agent", False)
        is_media_node = request.data.get("is_media_node", False)
        is_primary = request.data.get("is_primary", False)
        allow_user_workloads = request.data.get("allow_user_workloads", True)
        node_components = request.data.get("node_components", {})

        if not name or not host:
            return Response(
                {"error": "name and host are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.conf import settings
        from apps.deployments.models.core import PlatformConfig
        from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
        from apps.deployments.services.wireguard_service import WireGuardService

        config = PlatformConfig.load()
        base_domain = getattr(config, "server_domain", "") or "grid.smsly.cloud"
        server_ip = getattr(config, "server_ip", "") or os.environ.get("PUBLIC_IP", "")

        # ── 1. Assign node_number ──
        max_num = ManagedServer.objects.filter(is_primary=False).aggregate(
            m=db_models.Max("node_number")
        )["m"] or 0
        node_number = max_num + 1

        # ── 2. Compute node_domain using grid{N} naming ──
        domain_parts = base_domain.split(".")
        if len(domain_parts) > 2:
            node_domain = f"grid{node_number}.{'.'.join(domain_parts[1:])}"
        else:
            node_domain = f"grid{node_number}.{base_domain}"

        # ── 3. Create ManagedServer ──
        gateway_secret = secrets.token_hex(32)
        try:
            server = ManagedServer.objects.create(
                owner=request.user,
                name=name,
                host=host,
                node_type=node_type,
                is_lite_agent=is_lite_agent,
                is_primary=is_primary,
                allow_user_workloads=allow_user_workloads,
                node_components=node_components,
                node_number=node_number,
                node_domain=node_domain,
                gateway_secret=gateway_secret,
                provision_status=ManagedServer.ProvisionStatus.PENDING,
                status=ManagedServer.Status.UNKNOWN,
            )
        except DjangoValidationError as exc:
            return Response(
                {"error": getattr(exc, "message_dict", None) or str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 4. Pre-provision WireGuard peer on master ──
        wg_address = None
        wg_private_key = None
        wg_public_key = None
        master_wg_pubkey = ""
        master_wg_endpoint = ""

        try:
            mesh = MeshNetwork.objects.filter(
                name="default", is_active=True,
            ).first()
            if not mesh:
                mesh = MeshNetwork.objects.create(
                    name="default",
                    subnet="10.100.0.0/24",
                    listen_port=51820,
                    listen_port_fallback=33500,
                    interface_name="wg0",
                    is_active=True,
                )

            wg_private_key, wg_public_key = WireGuardService.generate_keypair()
            wg_address = mesh.next_available_ip()

            from apps.deployments.services.provisioner.helpers.server_config import (
                _get_master_wg_pubkey,
            )
            master_wg_pubkey = _get_master_wg_pubkey() or ""
            if server_ip:
                master_wg_endpoint = f"{server_ip}:{mesh.listen_port}"

            endpoint = ""
            if master_wg_endpoint:
                endpoint = WireGuardService.validate_endpoint(master_wg_endpoint)

            peer = WireGuardPeer.objects.create(
                mesh=mesh,
                server=server,
                private_key=wg_private_key,
                public_key=wg_public_key,
                wg_address=wg_address,
                endpoint=endpoint,
                allowed_ips=f"{wg_address}/32",
                is_active=True,
                is_local=False,
            )

            server.wg_address = wg_address
            server.save(update_fields=["wg_address", "updated_at"])

            local_peer = WireGuardPeer.objects.filter(
                mesh=mesh, is_local=True,
            ).first()
            if local_peer and local_peer.private_key:
                try:
                    WireGuardService.deploy_config(local_peer)
                except Exception as deploy_exc:
                    logger.warning(
                        "WG deploy to master failed for %s: %s",
                        server.name, deploy_exc,
                    )
        except Exception as exc:
            logger.warning(
                "WireGuard pre-provisioning failed for %s: %s", server.name, exc,
            )

        # ── 5. Pre-provision DNS A record ──
        dns_result = None
        try:
            cf_token = getattr(config, "cloudflare_api_token", "") or ""
            if cf_token and node_domain and server_ip:
                from apps.domains.services.dns import ensure_dns_records
                dns_result = ensure_dns_records(
                    [node_domain], server_ip, cf_token,
                )
        except Exception as exc:
            logger.warning(
                "DNS pre-provisioning failed for %s: %s", server.name, exc,
            )

        # ── 6. Generate signed bootstrap token ──
        app_secret = settings.SECRET_KEY.encode()
        payload_data = {
            "server_id": str(server.id),
            "name": name,
            "host": host,
            "node_type": node_type,
            "is_lite_agent": is_lite_agent,
            "is_media_node": is_media_node,
            "is_primary": is_primary,
            "allow_user_workloads": allow_user_workloads,
            "node_components": node_components,
            "node_number": node_number,
            "node_domain": node_domain,
            "wg_address": wg_address or "",
            "wg_private_key": wg_private_key or "",
            "wg_public_key": wg_public_key or "",
            "gateway_secret": gateway_secret,
            "master_wg_pubkey": master_wg_pubkey,
            "master_wg_endpoint": master_wg_endpoint,
            "exp": int(time.time()) + 3600,
            "nonce": secrets.token_hex(8),
        }
        payload_b64 = base64.urlsafe_b64encode(
            json_mod.dumps(payload_data).encode()
        ).decode()
        sig = hmac_mod.new(
            app_secret, payload_b64.encode(), hashlib.sha256,
        ).hexdigest()
        token = f"{payload_b64}.{sig}"

        master_url = os.environ.get("PUBLIC_URL", "https://grid.smsly.cloud")

        data = ManagedServerSerializer(server).data
        data.update({
            "token": token,
            "server_id": str(server.id),
            "node_number": node_number,
            "node_domain": node_domain,
            "wg_address": wg_address,
            "dns_result": dns_result,
            "master_url": master_url,
            "bootstrap_command": (
                f'curl -fsSL "{master_url}/api/v1/servers/bootstrap/{token}/" | bash'
            ),
        })
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="provision")
    @throttle_classes([ServerProvisionThrottle])
    def provision_new(self, request):
        is_primary_raw = request.data.get("is_primary", False)
        if isinstance(is_primary_raw, str):
            is_primary_requested = is_primary_raw.strip().lower() in (
                "true", "1", "yes", "t", "on",
            )
        else:
            is_primary_requested = bool(is_primary_raw)
        if is_primary_requested and not request.user.is_superuser:
            return Response(
                {"error": "Only superusers can provision a primary server."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ManagedServerProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated = serializer.validated_data.copy()
        auth_method = validated.pop("ssh_auth_method", "password")
        validated.pop("node_certificate", None)

        if validated.get("node_type") == "node":
            validated["is_lite_agent"] = False

        generated_public_key = None
        if auth_method == "generated":
            from apps.deployments.services.provisioner.helpers.ssh import _generate_ed25519_keypair
            priv_key_pem, generated_public_key = _generate_ed25519_keypair()
            validated["ssh_key"] = priv_key_pem

        try:
            server = ManagedServer.objects.create(
                owner=request.user,
                provision_status=ManagedServer.ProvisionStatus.PENDING,
                **validated,
            )
        except DjangoValidationError as exc:
            # Model pre_save signals (e.g. host policy in
            # signals/validation.py) raise django ValidationError on
            # save. Surface it as a 400, not a 500.
            return Response(
                {"error": getattr(exc, "message_dict", None) or str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if server.is_primary and server.allow_user_workloads:
            server.allow_user_workloads = False
            server.save(update_fields=["allow_user_workloads", "updated_at"])

        from apps.deployments.services.provisioner import provision_server
        provision_server.delay(str(server.id))

        data = ManagedServerSerializer(server).data
        if generated_public_key:
            data["generated_ssh_public_key"] = generated_public_key
        return Response(
            data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["post"], url_path="provision-batch")
    @throttle_classes([ServerProvisionThrottle])
    def provision_batch(self, request):
        servers_data = request.data.get("servers")
        if not servers_data or not isinstance(servers_data, list):
            return Response(
                {"error": "'servers' must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(servers_data) > 20:
            return Response(
                {"error": "Maximum 20 servers per batch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = []
        errors = []
        from apps.deployments.services.provisioner import provision_server
        from apps.deployments.services.provisioner.helpers.ssh import _generate_ed25519_keypair

        for idx, item in enumerate(servers_data):
            serializer = ManagedServerProvisionSerializer(data=item)
            if not serializer.is_valid():
                errors.append({"index": idx, "host": item.get("host", ""), "errors": serializer.errors})
                continue

            validated = serializer.validated_data.copy()
            auth_method = validated.pop("ssh_auth_method", "password")
            validated.pop("node_certificate", None)

            generated_public_key = None
            if auth_method == "generated":
                priv_key_pem, generated_public_key = _generate_ed25519_keypair()
                validated["ssh_key"] = priv_key_pem

            validated["is_lite_agent"] = True
            validated["is_primary"] = False

            try:
                server = ManagedServer.objects.create(
                    owner=request.user,
                    provision_status=ManagedServer.ProvisionStatus.PENDING,
                    **validated,
                )
            except DjangoValidationError as exc:
                errors.append({
                    "index": idx,
                    "host": item.get("host", ""),
                    "errors": getattr(exc, "message_dict", None) or str(exc),
                })
                continue
            provision_server.delay(str(server.id))
            entry = ManagedServerSerializer(server).data
            if generated_public_key:
                entry["generated_ssh_public_key"] = generated_public_key
            created.append(entry)

        return Response(
            {"created": created, "errors": errors, "total": len(created)},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"], url_path="provision-logs")
    def provision_logs(self, request, pk=None):
        server = self.get_object()
        return Response({
            "provision_status": server.provision_status,
            "provision_logs": server.provision_logs,
        })

    @action(detail=True, methods=["post"], url_path="retry-provision")
    @throttle_classes([ServerProvisionThrottle])
    def retry_provision(self, request, pk=None):
        server = self.get_object()

        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        server.provision_logs = f"--- Retry started by {request.user.username} at {timezone.now()} ---\n"
        server.save(update_fields=["provision_status", "provision_logs", "updated_at"])

        from apps.deployments.services.provisioner import provision_server
        provision_server.delay(str(server.id))

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="update-server")
    @throttle_classes([ServerProvisionThrottle])
    def update_server(self, request, pk=None):
        server = self.get_object()

        if not (server.ssh_key or server.ssh_password):
             return Response(
                 {"error": "Server has no SSH credentials configured for updates."},
                 status=status.HTTP_400_BAD_REQUEST,
             )

        blocked_statuses = {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.PROVISIONING,
            ManagedServer.ProvisionStatus.UPDATING,
        }
        if server.provision_status in blocked_statuses:
            provision_started_by = getattr(server, "_provision_started_by", None)
            if (
                not request.user.is_superuser
                and provision_started_by is not None
                and provision_started_by != request.user.id
            ):
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    "update_server: refusing to auto-clear provision_status=%s "
                    "for server %s — user %s is not the initiator (%s) and not "
                    "a superuser",
                    server.provision_status, server.id, request.user.id,
                    provision_started_by,
                )
                return Response(
                    {
                        "error": "Server is currently being provisioned by another user.",
                        "provision_status": server.provision_status,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "update_server: auto-clearing in-flight provision_status=%s for server %s (user=%s)",
                server.provision_status, server.id, request.user.id,
            )

        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        server.provision_logs = f"--- Update started by {request.user.username} at {timezone.now()} ---\n"
        server.save(update_fields=["provision_status", "provision_logs", "updated_at"])

        from apps.deployments.services.provisioner import provision_server
        provision_server.delay(str(server.id), skip_reboot=True)

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )
