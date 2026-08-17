"""
Provisioning mixins for ManagedServerViewSet.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
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
        """Generate a one-time bootstrap token for self-provisioning.

        The target server runs:
            curl -fsSL <master_url>/api/v1/servers/bootstrap/<token>/ | bash

        The token is an HMAC-signed payload encoding the server name,
        host, node type, and expiry. No SSH needed from the master.
        """
        import hmac as hmac_mod
        import hashlib
        import base64
        import json as json_mod

        name = request.data.get("name", "").strip()
        host = request.data.get("host", "").strip()
        node_type = request.data.get("node_type", "node")
        is_lite_agent = request.data.get("is_lite_agent", False)
        is_media_node = request.data.get("is_media_node", False)
        is_primary = request.data.get("is_primary", False)
        allow_user_workloads = request.data.get("allow_user_workloads", True)
        ssh_user = request.data.get("ssh_user", "root")
        ssh_port = request.data.get("ssh_port", 22)

        if not name or not host:
            return Response(
                {"error": "name and host are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.conf import settings
        secret = settings.SECRET_KEY.encode()
        payload_data = {
            "name": name,
            "host": host,
            "node_type": node_type,
            "is_lite_agent": is_lite_agent,
            "is_media_node": is_media_node,
            "is_primary": is_primary,
            "allow_user_workloads": allow_user_workloads,
            "ssh_user": ssh_user,
            "ssh_port": ssh_port,
            "exp": int(time.time()) + 3600,
            "nonce": secrets.token_hex(8),
        }
        payload_b64 = base64.urlsafe_b64encode(
            json_mod.dumps(payload_data).encode()
        ).decode()
        sig = hmac_mod.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()
        token = f"{payload_b64}.{sig}"

        master_url = os.environ.get("PUBLIC_URL", "https://grid.smsly.cloud")

        return Response(
            {
                "token": token,
                "master_url": master_url,
                "bootstrap_command": f'curl -fsSL "{master_url}/api/v1/servers/bootstrap/{token}/" | bash',
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
