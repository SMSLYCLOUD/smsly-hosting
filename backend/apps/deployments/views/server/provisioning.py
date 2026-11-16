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
    @throttle_classes([ServerProvisionThrottle])
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
        node_type = (request.data.get("node_type") or "").strip() or "node"
        is_lite_agent = request.data.get("is_lite_agent", False)
        is_media_node = request.data.get("is_media_node", False)
        # Normalize: flags win when node_type absent/mismatched (older UI
        # sends booleans only). media > agent-lite > node.
        if is_media_node:
            node_type, is_lite_agent = "media", False
        elif is_lite_agent:
            node_type = "agent-lite"
        elif node_type == "agent-lite":
            is_lite_agent = True
        elif node_type == "media":
            is_media_node, is_lite_agent = True, False
        is_primary = request.data.get("is_primary", False)
        allow_user_workloads = request.data.get("allow_user_workloads", True)
        node_components = request.data.get("node_components", {})

        if not name or not host:
            return Response(
                {"error": "name and host are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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

        from django.conf import settings
        from apps.deployments.models.core import PlatformConfig
        from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
        from apps.deployments.services.wireguard_service import WireGuardService

        config = PlatformConfig.load()
        base_domain = (
            getattr(config, "server_domain", "")
            or getattr(config, "domain", "")
            or "grid.smsly.cloud"
        )
        server_ip = getattr(config, "server_ip", "") or os.environ.get("PUBLIC_IP", "")

        # ── 1+2+3+4. Assign node_number, compute node_domain, create the
        #    server, and pre-provision the WireGuard peer — all inside ONE
        #    database transaction serialized on the mesh row. Without the
        #    lock, two concurrent provision-token calls compute the same
        #    max(node_number) and the same next_available_ip(), producing
        #    duplicate node numbers/domains and clashing WG addresses. The
        #    local (master) peer is ensured FIRST so a fresh platform never
        #    hands 10.100.0.1 to a node — .1 is the master's conventional
        #    mesh address. Network I/O (WG config deploy) stays outside.
        from apps.deployments.services.provisioner.helpers.server_config import (
            _get_master_wg_pubkey,
            allocate_node_number,
        )
        from django.db import transaction
        try:
            with transaction.atomic():
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
                mesh_locked = MeshNetwork.objects.select_for_update().get(
                    id=mesh.id
                )

                node_number, _ = allocate_node_number()

                domain_parts = base_domain.split(".")
                if len(domain_parts) > 2:
                    node_domain = f"grid{node_number}.{'.'.join(domain_parts[1:])}"
                else:
                    node_domain = f"grid{node_number}.{base_domain}"

                gateway_secret = secrets.token_hex(32)
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
                    provider_metadata={
                        "provision_started_by": request.user.id,
                    },
                )

                wg_private_key, wg_public_key = None, None
                wg_address = None
                master_wg_pubkey = ""
                master_wg_endpoint = ""
                try:
                    # Savepoint: a WG failure rolls back just the peer
                    # (server + number survive, token is issued without
                    # VPN — same degraded mode as before).
                    with transaction.atomic():
                        wg_private_key, wg_public_key = (
                            WireGuardService.generate_keypair()
                        )
                        # Local (master) peer first — reserves .1.
                        WireGuardService.add_peer_to_mesh(
                            mesh_locked, server=None, is_local=True,
                        )
                        wg_address = mesh_locked.next_available_ip()
                        master_wg_pubkey = _get_master_wg_pubkey() or ""
                        if server_ip:
                            master_wg_endpoint = (
                                f"{server_ip}:{mesh_locked.listen_port}"
                            )

                        node_endpoint = ""
                        try:
                            node_endpoint = WireGuardService.validate_endpoint(
                                f"{host}:{mesh_locked.listen_port}"
                            )
                        except ValueError:
                            # The pre_save host allow-list constrains this,
                            # but never fail provisioning over endpoint text.
                            logger.warning(
                                "provision-token: host %r is not a valid WG "
                                "endpoint; leaving peer endpoint empty.",
                                host,
                            )

                        WireGuardPeer.objects.create(
                            mesh=mesh_locked,
                            server=server,
                            private_key=wg_private_key,
                            public_key=wg_public_key,
                            wg_address=wg_address,
                            endpoint=node_endpoint,
                            allowed_ips=f"{wg_address}/32",
                            is_active=True,
                            is_local=False,
                        )

                        server.wg_address = wg_address
                        server.save(update_fields=["wg_address", "updated_at"])
                except Exception as wg_exc:
                    wg_private_key, wg_public_key = None, None
                    wg_address = None
                    master_wg_pubkey = ""
                    master_wg_endpoint = ""
                    logger.warning(
                        "WireGuard pre-provisioning failed for %s: %s",
                        server.name, wg_exc,
                    )
                mesh = mesh_locked
        except DjangoValidationError as exc:
            return Response(
                {"error": getattr(exc, "message_dict", None) or str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Deploy the refreshed local WG config (network I/O — outside the
        # transaction above).
        try:
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
        # The record must point at the NODE's address (host) — never the
        # master's server_ip. Only literal IPs are valid A-record targets;
        # a hostname host is skipped (its own DNS already resolves it).
        dns_result = None
        dns_note = ""
        try:
            import ipaddress as _ipaddress

            dns_target = ""
            try:
                dns_target = str(_ipaddress.ip_address(host))
            except ValueError:
                dns_note = (
                    f"host {host!r} is a hostname, skipping node-domain "
                    "A-record pre-provisioning (its own DNS already resolves it)."
                )
                logger.info("provision-token: %s", dns_note)
            cf_token = getattr(config, "cloudflare_api_token", "") or ""
            if not cf_token:
                dns_note = (dns_note + " " if dns_note else "") + (
                    "No Cloudflare token configured — DNS not pre-provisioned."
                )
            if dns_target and cf_token and node_domain:
                from apps.domains.services.dns import ensure_dns_records
                dns_result = ensure_dns_records(
                    [node_domain], dns_target, cf_token,
                )
        except Exception as exc:
            logger.warning(
                "DNS pre-provisioning failed for %s: %s", server.name, exc,
            )
            dns_note = f"DNS pre-provisioning failed: {exc}"

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
            "dns_note": dns_note,
            "master_url": master_url,
            "bootstrap_command": (
                f'curl -fsSL "{master_url}/api/v1/servers/bootstrap/{token}/" | bash'
            ),
        })
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reissue-token")
    @throttle_classes([ServerProvisionThrottle])
    def reissue_token(self, request, pk=None):
        """Mint a fresh 1h bootstrap token for a PENDING/FAILED node.

        Closes the 11h dead window where the original token expired but
        the record is retained: the operator re-issues instead of
        deleting/recreating the server (which would churn node_number,
        WG address, and DNS).
        """
        server = self.get_object()
        if server.provision_status not in {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.FAILED,
        }:
            return Response(
                {
                    "error": "Token re-issue only for PENDING/FAILED servers.",
                    "provision_status": server.provision_status,
                },
                status=status.HTTP_409_CONFLICT,
            )
        from django.db import transaction as _txn
        with _txn.atomic():
            server = ManagedServer.objects.select_for_update().get(id=server.id)
            payload_data = {
                "server_id": str(server.id),
                "name": server.name,
                "host": server.host,
                "node_type": getattr(server, "node_type", "node"),
                "is_lite_agent": bool(getattr(server, "is_lite_agent", False)),
                "is_media_node": getattr(server, "node_type", "") == "media",
                "is_primary": bool(getattr(server, "is_primary", False)),
                "allow_user_workloads": bool(getattr(server, "allow_user_workloads", True)),
                "node_components": dict(getattr(server, "node_components", None) or {}),
                "node_number": getattr(server, "node_number", None),
                "node_domain": getattr(server, "node_domain", ""),
                "wg_address": str(getattr(server, "wg_address", "") or ""),
                "wg_private_key": "",
                "wg_public_key": "",
                "gateway_secret": str(getattr(server, "gateway_secret", "") or ""),
                "master_wg_pubkey": "",
                "master_wg_endpoint": "",
                "exp": int(time.time()) + 3600,
                "nonce": secrets.token_hex(8),
            }
            if not payload_data["gateway_secret"]:
                payload_data["gateway_secret"] = secrets.token_hex(32)
                server.gateway_secret = payload_data["gateway_secret"]
                server.save(update_fields=["gateway_secret", "updated_at"])
            payload_b64 = base64.urlsafe_b64encode(
                json_mod.dumps(payload_data).encode()
            ).decode()
            sig = hmac_mod.new(
                settings.SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256,
            ).hexdigest()
            token = f"{payload_b64}.{sig}"
        master_url = os.environ.get("PUBLIC_URL", "https://grid.smsly.cloud")
        return Response(
            {
                "token": token,
                "server_id": str(server.id),
                "bootstrap_command": (
                    f'curl -fsSL "{master_url}/api/v1/servers/bootstrap/{token}/" | bash'
                ),
            },
            status=status.HTTP_200_OK,
        )

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
        elif validated.get("node_type") == "agent-lite":
            # node_type is the UI-facing selector; the flag drives
            # server_install_mode. Normalize so the two cannot disagree
            # (previously agent-lite + flag False silently provisioned
            # a full "node" instead).
            validated["is_lite_agent"] = True

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

            validated["is_lite_agent"] = validated.get("is_lite_agent", True)
            validated["is_primary"] = False
            # Normalize node_type ↔ flag like provision_new so an explicit
            # node_type='node' + is_lite_agent=False batch entry provisions
            # a full node instead of silently becoming lite.
            if validated.get("node_type") == "node":
                validated["is_lite_agent"] = False
            elif validated.get("node_type") == "agent-lite":
                validated["is_lite_agent"] = True

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
                entry["generated_key_warning"] = (
                    "Private key stored encrypted; public key shown once — "
                    "install it on the host now, it will not be shown again."
                )
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

        _inflight = {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.PROVISIONING,
            ManagedServer.ProvisionStatus.UPDATING,
        }
        _force = str(request.data.get("force", "") or "").lower() in {"1", "true", "yes", "on"}
        if server.provision_status in _inflight and not _force:
            return Response(
                {
                    "error": "Server provisioning already in flight.",
                    "provision_status": server.provision_status,
                },
                status=status.HTTP_409_CONFLICT,
            )
        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        prior = (server.provision_logs or "")[-50000:] if server.provision_logs else ""
        server.provision_logs = (
            (prior + f"\n--- Retry started by {request.user.username} at {timezone.now()} ---\n")
            if prior else f"--- Retry started by {request.user.username} at {timezone.now()} ---\n"
        )
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
            _force_update = str(request.data.get("force", "") or "").lower() in {"1", "true", "yes", "on"}
            if not _force_update:
                return Response(
                    {
                        "error": "Server update already in flight.",
                        "provision_status": server.provision_status,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            provision_started_by = (server.provider_metadata or {}).get("provision_started_by")
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

        from django.core.cache import cache as _update_cache
        _update_lock = f"server-update-request:{server.id}"
        if not _update_cache.add(_update_lock, "1", timeout=60):
            return Response(
                {
                    "error": "Update already requested in the last 60s (double-click guard).",
                    "provision_status": server.provision_status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        prior = (server.provision_logs or "")[-50000:] if server.provision_logs else ""
        server.provision_logs = (
            (prior + f"\n--- Update started by {request.user.username} at {timezone.now()} ---\n")
            if prior else f"--- Update started by {request.user.username} at {timezone.now()} ---\n"
        )
        server.save(update_fields=["provision_status", "provision_logs", "updated_at"])

        from apps.deployments.services.provisioner import provision_server
        provision_server.delay(str(server.id), skip_reboot=True)

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )
